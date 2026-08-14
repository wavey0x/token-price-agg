from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import Literal

import httpx
from PIL import Image, UnidentifiedImageError

from price_api.token_metadata.logo_overrides import get_logo_override
from price_api.token_metadata.logo_sources import LogoCandidate, TokenLogoSourceManager

MAX_IMAGE_BYTES = 256 * 1024
MAX_IMAGE_SIDE = 1024
MAX_IMAGE_PIXELS = 1_048_576
_ALLOWED_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}
_HEADERS = {"User-Agent": "price-api/token-logo-acquirer"}


class LogoValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ValidatedLogo:
    image_bytes: bytes
    content_hash: str
    mime_type: str


@dataclass(frozen=True)
class AcquisitionResult:
    outcome: Literal["success", "transient", "unavailable"]
    asset: ValidatedLogo | None
    source: str | None
    http_status: int | None
    error_code: str
    retry_after_ms: int | None = None


@dataclass(frozen=True)
class _CandidateResult:
    outcome: Literal["success", "transient", "conclusive"]
    asset: ValidatedLogo | None
    source: str
    http_status: int | None
    error_code: str
    retry_after_ms: int | None = None


class TokenLogoAcquirer:
    def __init__(
        self,
        *,
        source_manager: TokenLogoSourceManager,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._source_manager = source_manager
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0),
            follow_redirects=False,
            trust_env=False,
            headers=_HEADERS,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def acquire(self, *, chain_id: int, address: str) -> AcquisitionResult:
        attempts: list[_CandidateResult] = []

        override = get_logo_override(chain_id=chain_id, address=address)
        if override is not None:
            try:
                mime_type = _signature_mime(override.image_bytes)
                asset = validate_logo_bytes(override.image_bytes, mime_type=mime_type)
                return AcquisitionResult(
                    outcome="success",
                    asset=asset,
                    source=override.source,
                    http_status=None,
                    error_code="success",
                )
            except LogoValidationError as exc:
                attempts.append(
                    _CandidateResult(
                        outcome="conclusive",
                        asset=None,
                        source=override.source,
                        http_status=None,
                        error_code=exc.code,
                    )
                )

        for candidate in self._source_manager.get_candidates(
            chain_id=chain_id,
            address=address,
        ):
            result = await self._acquire_candidate(
                chain_id=chain_id,
                address=address,
                candidate=candidate,
            )
            attempts.append(result)
            if result.outcome == "success":
                return AcquisitionResult(
                    outcome="success",
                    asset=result.asset,
                    source=result.source,
                    http_status=result.http_status,
                    error_code="success",
                )

        transient = next((attempt for attempt in attempts if attempt.outcome == "transient"), None)
        if transient is not None:
            retry_after = max(
                (
                    attempt.retry_after_ms or 0
                    for attempt in attempts
                    if attempt.outcome == "transient"
                ),
                default=0,
            )
            return AcquisitionResult(
                outcome="transient",
                asset=None,
                source=None,
                http_status=transient.http_status,
                error_code=transient.error_code,
                retry_after_ms=retry_after or None,
            )

        last = attempts[-1] if attempts else None
        return AcquisitionResult(
            outcome="unavailable",
            asset=None,
            source=None,
            http_status=last.http_status if last is not None else None,
            error_code=last.error_code if last is not None else "no_candidate",
        )

    async def _acquire_candidate(
        self,
        *,
        chain_id: int,
        address: str,
        candidate: LogoCandidate,
    ) -> _CandidateResult:
        source = self._source_manager.source_for_id(candidate.source)
        if source is None:
            return _conclusive(candidate, "source_not_registered")
        if not source.allows_image_url(
            chain_id=chain_id,
            address=address,
            url=candidate.url,
        ):
            return _conclusive(candidate, "source_policy_violation")

        try:
            async with self._client.stream("GET", candidate.url) as response:
                status = response.status_code
                if status != 200:
                    outcome: Literal["transient", "conclusive"] = (
                        "transient" if _is_transient_status(status) else "conclusive"
                    )
                    return _CandidateResult(
                        outcome=outcome,
                        asset=None,
                        source=candidate.source,
                        http_status=status,
                        error_code=f"http_{status}",
                        retry_after_ms=(
                            _retry_after_ms(response.headers.get("Retry-After"))
                            if status in {429, 503}
                            else None
                        ),
                    )

                content_length = _content_length(response)
                if content_length is not None and content_length > MAX_IMAGE_BYTES:
                    return _conclusive(candidate, "body_too_large", http_status=status)

                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_IMAGE_BYTES:
                        return _conclusive(candidate, "body_too_large", http_status=status)

                mime_type = _response_mime(response)
                try:
                    asset = validate_logo_bytes(bytes(body), mime_type=mime_type)
                except LogoValidationError as exc:
                    return _conclusive(candidate, exc.code, http_status=status)
                return _CandidateResult(
                    outcome="success",
                    asset=asset,
                    source=candidate.source,
                    http_status=status,
                    error_code="success",
                )
        except httpx.TimeoutException:
            return _CandidateResult(
                outcome="transient",
                asset=None,
                source=candidate.source,
                http_status=None,
                error_code="network_timeout",
            )
        except httpx.RemoteProtocolError:
            return _CandidateResult(
                outcome="transient",
                asset=None,
                source=candidate.source,
                http_status=None,
                error_code="remote_protocol_error",
            )
        except httpx.NetworkError:
            return _CandidateResult(
                outcome="transient",
                asset=None,
                source=candidate.source,
                http_status=None,
                error_code="network_error",
            )
        except httpx.HTTPError:
            return _CandidateResult(
                outcome="transient",
                asset=None,
                source=candidate.source,
                http_status=None,
                error_code="http_client_error",
            )


def validate_logo_bytes(data: bytes, *, mime_type: str) -> ValidatedLogo:
    if not data:
        raise LogoValidationError("empty_body")
    if len(data) > MAX_IMAGE_BYTES:
        raise LogoValidationError("body_too_large")
    expected_format = _ALLOWED_FORMATS.get(mime_type)
    if expected_format is None:
        raise LogoValidationError("unsupported_media")
    if _signature_mime(data) != mime_type:
        raise LogoValidationError("signature_mismatch")

    try:
        with Image.open(BytesIO(data)) as image:
            detected_format = image.format
            width, height = image.size
            frame_count = int(getattr(image, "n_frames", 1))
            if detected_format != expected_format:
                raise LogoValidationError("decoder_format_mismatch")
            if frame_count != 1:
                raise LogoValidationError("multiple_frames")
            if width <= 0 or height <= 0:
                raise LogoValidationError("invalid_dimensions")
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                raise LogoValidationError("dimensions_too_large")
            if width * height > MAX_IMAGE_PIXELS:
                raise LogoValidationError("pixel_count_too_large")
            image.verify()

        with Image.open(BytesIO(data)) as image:
            if int(getattr(image, "n_frames", 1)) != 1:
                raise LogoValidationError("multiple_frames")
            image.load()
    except LogoValidationError:
        raise
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise LogoValidationError("corrupt_image") from exc

    return ValidatedLogo(
        image_bytes=data,
        content_hash=hashlib.sha256(data).hexdigest(),
        mime_type=mime_type,
    )


def _signature_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise LogoValidationError("signature_mismatch")


def _response_mime(response: httpx.Response) -> str:
    value = str(response.headers.get("Content-Type", ""))
    return value.split(";", 1)[0].strip().lower()


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return max(parsed, 0)


def _is_transient_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def _retry_after_ms(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped) * 1000
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = parsed - datetime.now(UTC)
    return max(int(delta.total_seconds() * 1000), 0)


def _conclusive(
    candidate: LogoCandidate,
    error_code: str,
    *,
    http_status: int | None = None,
) -> _CandidateResult:
    return _CandidateResult(
        outcome="conclusive",
        asset=None,
        source=candidate.source,
        http_status=http_status,
        error_code=error_code[:64],
    )

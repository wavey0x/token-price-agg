from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx

from token_price_agg.core.models import TokenMetadata
from token_price_agg.token_metadata.logo_urls import LogoCandidate, build_logo_candidates

_LOGGER = logging.getLogger("token_price_agg.token_metadata")

_HTTP_TIMEOUT_S = 2.0
_HTTP_HEADERS = {"User-Agent": "token-price-agg/logo-verifier"}
_MAX_REDIRECTS = 3
_MAX_RESPONSE_BYTES = 1_048_576  # 1 MB


@dataclass(frozen=True)
class VerifyAttempt:
    url: str
    source: str
    method: str
    http_status: int | None
    valid: bool
    error: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    logo_url: str | None
    logo_status: str
    logo_source: str | None
    logo_checked_at: int
    logo_http_status: int | None
    attempts: list[VerifyAttempt]


def is_safe_logo_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    if hostname in ("localhost", "localhost.localdomain"):
        return False

    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False
    except ValueError:
        pass

    return True


async def verify_candidates(
    candidates: list[LogoCandidate],
) -> VerifyResult:
    timeout = httpx.Timeout(_HTTP_TIMEOUT_S)
    attempts: list[VerifyAttempt] = []
    selected: LogoCandidate | None = None
    selected_status: int | None = None

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        headers=_HTTP_HEADERS,
    ) as client:
        for candidate in candidates:
            if not is_safe_logo_url(candidate.url):
                attempts.append(
                    VerifyAttempt(
                        url=candidate.url,
                        source=candidate.source,
                        method="SKIP",
                        http_status=None,
                        valid=False,
                        error="unsafe_url",
                    )
                )
                continue

            ok_head, status_head, error_head = await _check_candidate(
                client=client,
                candidate=candidate,
                method="HEAD",
            )
            attempts.append(
                VerifyAttempt(
                    url=candidate.url,
                    source=candidate.source,
                    method="HEAD",
                    http_status=status_head,
                    valid=ok_head,
                    error=error_head,
                )
            )
            if ok_head:
                selected = candidate
                selected_status = status_head
                break

            ok_get, status_get, error_get = await _check_candidate(
                client=client,
                candidate=candidate,
                method="GET",
            )
            attempts.append(
                VerifyAttempt(
                    url=candidate.url,
                    source=candidate.source,
                    method="GET",
                    http_status=status_get,
                    valid=ok_get,
                    error=error_get,
                )
            )
            if ok_get:
                selected = candidate
                selected_status = status_get
                break

    now = int(time.time())
    if selected is not None:
        return VerifyResult(
            logo_url=selected.url,
            logo_status="valid",
            logo_source=selected.source,
            logo_checked_at=now,
            logo_http_status=selected_status,
            attempts=attempts,
        )

    return VerifyResult(
        logo_url=None,
        logo_status="invalid",
        logo_source=None,
        logo_checked_at=now,
        logo_http_status=_last_http_status(attempts),
        attempts=attempts,
    )


def apply_verify_result(
    base: TokenMetadata,
    result: VerifyResult,
) -> TokenMetadata:
    return base.model_copy(
        update={
            "logo_url": result.logo_url,
            "logo_status": result.logo_status,
            "logo_source": result.logo_source,
            "logo_checked_at": result.logo_checked_at,
            "logo_http_status": result.logo_http_status,
        }
    )


async def _check_candidate(
    *,
    client: httpx.AsyncClient,
    candidate: LogoCandidate,
    method: str,
) -> tuple[bool, int | None, str | None]:
    try:
        if method == "HEAD":
            response = await client.head(candidate.url)
        else:
            response = await client.get(candidate.url)
    except httpx.HTTPError as exc:
        return False, None, type(exc).__name__

    valid = _is_valid_image_response(response)
    return valid, response.status_code, None


def _is_valid_image_response(response: httpx.Response) -> bool:
    if response.status_code != 200:
        return False

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type.startswith("image/"):
        return True

    body = response.content[:32]
    return (
        body.startswith(b"\x89PNG\r\n\x1a\n")
        or body.startswith(b"\xff\xd8\xff")
        or body.startswith(b"GIF87a")
        or body.startswith(b"GIF89a")
        or body.startswith(b"RIFF")
        or body.lstrip().startswith(b"<svg")
    )


def _last_http_status(attempts: list[VerifyAttempt]) -> int | None:
    for attempt in reversed(attempts):
        if attempt.http_status is not None:
            return attempt.http_status
    return None

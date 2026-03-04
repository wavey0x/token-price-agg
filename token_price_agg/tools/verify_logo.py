from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass

import httpx

from token_price_agg.app.config import get_settings
from token_price_agg.core.models import TokenMetadata
from token_price_agg.core.validator import AddressValidator
from token_price_agg.token_metadata.cache import TokenMetadataCache
from token_price_agg.token_metadata.logo_urls import LogoCandidate, build_logo_candidates

_HTTP_TIMEOUT_S = 2.0
_HTTP_HEADERS = {"User-Agent": "token-price-agg/logo-verifier"}


@dataclass(frozen=True)
class VerifyAttempt:
    url: str
    source: str
    method: str
    http_status: int | None
    valid: bool
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Force-refresh token logo URL validity")
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--token", type=str, required=True, help="Token address")
    return parser.parse_args()


async def verify_token_logo(*, chain_id: int, token: str) -> dict[str, object]:
    address = AddressValidator.normalize_address(token)
    settings = get_settings()
    cache = TokenMetadataCache(db_path=settings.token_metadata_db_path)

    existing = cache.get_many(chain_id=chain_id, addresses=[address]).get(address)
    candidates = build_logo_candidates(
        chain_id=chain_id,
        address=address,
        provider_logo_url=None,
        cached_logo_url=existing.logo_url if existing is not None else None,
    )

    timeout = httpx.Timeout(_HTTP_TIMEOUT_S)
    attempts: list[VerifyAttempt] = []
    selected: LogoCandidate | None = None
    selected_status: int | None = None

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=_HTTP_HEADERS,
    ) as client:
        for candidate in candidates:
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
    base = existing or TokenMetadata(
        chain_id=chain_id,
        address=address,
    )
    if selected is not None:
        updated = base.model_copy(
            update={
                "logo_url": selected.url,
                "logo_status": "valid",
                "logo_checked_at": now,
                "logo_http_status": selected_status,
            }
        )
        result_status = "valid"
    else:
        last_status = _last_http_status(attempts)
        updated = base.model_copy(
            update={
                "logo_url": None,
                "logo_status": "invalid",
                "logo_checked_at": now,
                "logo_http_status": last_status,
            }
        )
        result_status = "invalid"

    cache.upsert_many([updated])

    return {
        "chain_id": chain_id,
        "token": address,
        "result": result_status,
        "logo_url": updated.logo_url,
        "logo_status": updated.logo_status,
        "logo_checked_at": updated.logo_checked_at,
        "logo_http_status": updated.logo_http_status,
        "attempts": [asdict(item) for item in attempts],
    }


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


def main() -> int:
    args = parse_args()
    try:
        payload = asyncio.run(
            verify_token_logo(
                chain_id=args.chain_id,
                token=args.token,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps({"ok": True, **payload}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

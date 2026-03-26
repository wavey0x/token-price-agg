from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from token_price_agg.app.config import get_settings
from token_price_agg.core.models import TokenMetadata
from token_price_agg.core.validator import AddressValidator
from token_price_agg.token_metadata.cache import TokenMetadataCache
from token_price_agg.token_metadata.logo_sources import TokenLogoSourceManager
from token_price_agg.token_metadata.logo_urls import build_logo_candidates
from token_price_agg.token_metadata.logo_verifier import (
    apply_verify_result,
    verify_candidates,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Force-refresh token logo URL validity")
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--token", type=str, required=True, help="Token address")
    return parser.parse_args()


async def verify_token_logo(*, chain_id: int, token: str) -> dict[str, object]:
    address = AddressValidator.normalize_address(token)
    settings = get_settings()
    cache = TokenMetadataCache(db_path=settings.token_metadata_db_path)
    source_manager = TokenLogoSourceManager(cache=cache)

    await source_manager.refresh_sources(chain_id=chain_id)

    existing = cache.get_many(chain_id=chain_id, addresses=[address]).get(address)
    source_logo_candidates = source_manager.get_candidates(
        chain_id=chain_id,
        addresses=[address],
    )
    candidates = build_logo_candidates(
        chain_id=chain_id,
        address=address,
        provider_logo_urls=None,
        cached_logo_url=existing.logo_url if existing is not None else None,
        additional_logo_candidates=source_logo_candidates.get(address),
    )

    result = await verify_candidates(candidates)

    base = existing or TokenMetadata(chain_id=chain_id, address=address)
    updated = apply_verify_result(base, result)
    cache.upsert_many([updated])

    return {
        "chain_id": chain_id,
        "token": address,
        "result": result.logo_status,
        "logo_url": result.logo_url,
        "logo_status": result.logo_status,
        "logo_source": result.logo_source,
        "logo_checked_at": result.logo_checked_at,
        "logo_http_status": result.logo_http_status,
        "attempts": [asdict(item) for item in result.attempts],
    }


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

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from token_price_agg.app.config import get_settings
from token_price_agg.token_metadata.cache import TokenMetadataCache
from token_price_agg.token_metadata.logo_sources import TokenLogoSourceManager


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh token logo source caches")
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh sources even if the local sync state is still fresh",
    )
    return parser.parse_args()


async def refresh_logo_sources(*, chain_id: int, force: bool) -> dict[str, object]:
    settings = get_settings()
    cache = TokenMetadataCache(db_path=settings.token_metadata_db_path)
    manager = TokenLogoSourceManager(cache=cache)
    refreshed = await manager.refresh_sources(chain_id=chain_id, force=force)
    return {
        "chain_id": chain_id,
        "force": force,
        "sources": refreshed,
    }


def main() -> int:
    args = parse_args()
    try:
        payload = asyncio.run(
            refresh_logo_sources(
                chain_id=args.chain_id,
                force=args.force,
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

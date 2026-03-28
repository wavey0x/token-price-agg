from __future__ import annotations

from pathlib import Path

import pytest

from token_price_agg.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache
from token_price_agg.token_metadata.logo_sources import (
    CoinGeckoTokenListSource,
    LocalTokenLogoOverrideSource,
    TokenLogoSourceManager,
)

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
OPASF = "0x7fE24F1A024D33506966CB7CA48Bab8c65fB632d"


def test_coingecko_source_parses_entries_and_filters_invalid_rows() -> None:
    payload = {
        "tokens": [
            {
                "chainId": 1,
                "address": USDC.lower(),
                "logoURI": "https://assets.coingecko.com/usdc.png",
            },
            {
                "chainId": 1,
                "address": USDC,
                "logoURI": "https://assets.coingecko.com/usdc-duplicate.png",
            },
            {
                "chainId": 137,
                "address": USDC,
                "logoURI": "https://assets.coingecko.com/usdc-polygon.png",
            },
            {
                "chainId": 1,
                "address": "not-an-address",
                "logoURI": "https://assets.coingecko.com/invalid.png",
            },
            {
                "chainId": 1,
                "address": USDC,
                "logoURI": "http://assets.coingecko.com/insecure.png",
            },
        ]
    }

    entries = CoinGeckoTokenListSource._parse_entries(chain_id=1, payload=payload)

    assert entries == [
        TokenLogoSourceEntry(
            source="coingecko",
            chain_id=1,
            address=USDC,
            logo_url="https://assets.coingecko.com/usdc.png",
        )
    ]


def test_logo_source_manager_returns_cached_candidates(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    cache.replace_logo_source_entries(
        source="coingecko",
        chain_id=1,
        entries=[
            TokenLogoSourceEntry(
                source="coingecko",
                chain_id=1,
                address=USDC,
                logo_url="https://assets.coingecko.com/usdc.png",
            )
        ],
    )

    manager = TokenLogoSourceManager(cache=cache)
    candidates = manager.get_candidates(chain_id=1, addresses=[USDC])

    assert candidates[USDC][0].source == "coingecko"
    assert candidates[USDC][0].url == "https://assets.coingecko.com/usdc.png"


@pytest.mark.asyncio
async def test_logo_source_manager_refreshes_local_overrides(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    manager = TokenLogoSourceManager(
        cache=cache,
        sources=[LocalTokenLogoOverrideSource()],
    )

    refreshed = await manager.refresh_sources(chain_id=1, force=True)
    candidates = manager.get_candidates(chain_id=1, addresses=[OPASF])
    sync_state = cache.get_logo_source_sync_state(source="local_override", chain_id=1)

    assert refreshed == {"local_override": 1}
    assert candidates[OPASF][0].source == "local_override"
    assert candidates[OPASF][0].url == "https://www.asymmetry.finance/ASF-32x32.png"
    assert sync_state is not None

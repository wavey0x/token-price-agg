from __future__ import annotations

from pathlib import Path

from token_price_agg.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache
from token_price_agg.token_metadata.logo_sources import (
    CoinGeckoTokenListSource,
    TokenLogoSourceManager,
)

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


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

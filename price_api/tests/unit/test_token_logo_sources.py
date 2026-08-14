from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from price_api.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache
from price_api.token_metadata.logo_sources import (
    LOGO_SOURCES,
    CoinGeckoSource,
    SmolDappSource,
    TokenLogoSourceManager,
    TrustWalletSource,
    YearnTokenAssetsSource,
)

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_registry_is_the_complete_priority_boundary() -> None:
    assert [source.id for source in LOGO_SOURCES] == [
        "yearn_tokenassets",
        "trustwallet",
        "coingecko",
        "smoldapp",
    ]


@pytest.mark.parametrize(
    ("source", "valid_url"),
    [
        (
            YearnTokenAssetsSource(),
            "https://raw.githubusercontent.com/yearn/tokenAssets/main/"
            "tokens/1/0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48/logo-128.png",
        ),
        (
            TrustWalletSource(),
            "https://raw.githubusercontent.com/trustwallet/assets/master/"
            "blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/"
            "logo.png",
        ),
        (
            SmolDappSource(),
            "https://cdn.jsdelivr.net/gh/SmolDapp/tokenAssets@main/"
            "tokens/1/0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48/logo-128.png",
        ),
    ],
)
def test_deterministic_source_policy_is_exact(source: object, valid_url: str) -> None:
    assert source.allows_image_url(chain_id=1, address=USDC, url=valid_url)  # type: ignore[attr-defined]
    for escaped in (
        valid_url.replace("https://", "http://", 1),
        valid_url.replace("https://", "https://user:secret@", 1),
        valid_url.replace("//", "//", 1).replace(
            valid_url.split("/", 3)[2],
            f"{valid_url.split('/', 3)[2]}:444",
            1,
        ),
        valid_url.replace("logo", "../logo", 1),
        f"{valid_url}?redirect=https://example.com",
    ):
        assert not source.allows_image_url(  # type: ignore[attr-defined]
            chain_id=1,
            address=USDC,
            url=escaped,
        )


def test_coingecko_policy_accepts_only_reviewed_image_origins_and_paths() -> None:
    source = CoinGeckoSource()
    valid = "https://assets.coingecko.com/coins/images/6319/thumb/usdc.png?1696506694"
    assert source.allows_image_url(chain_id=1, address=USDC, url=valid)
    assert not source.allows_image_url(
        chain_id=1,
        address=USDC,
        url="https://assets.coingecko.com/other/usdc.png",
    )
    assert not source.allows_image_url(
        chain_id=1,
        address=USDC,
        url="https://evil.example/coins/images/6319/thumb/usdc.png",
    )
    assert not source.allows_image_url(
        chain_id=1,
        address=USDC,
        url="https://assets.coingecko.com:444/coins/images/6319/thumb/usdc.png",
    )


def test_coingecko_parser_drops_unreviewed_logo_urls() -> None:
    source = CoinGeckoSource()
    entries = source.parse_metadata(
        chain_id=1,
        payload={
            "tokens": [
                {
                    "chainId": 1,
                    "address": USDC.lower(),
                    "logoURI": ("https://assets.coingecko.com/coins/images/6319/thumb/usdc.png"),
                },
                {
                    "chainId": 1,
                    "address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
                    "logoURI": "https://unreviewed.example/dai.png",
                },
            ]
        },
    )
    assert entries == [
        TokenLogoSourceEntry(
            source="coingecko",
            chain_id=1,
            address=USDC,
            logo_url="https://assets.coingecko.com/coins/images/6319/thumb/usdc.png",
        )
    ]


def test_manager_ignores_unregistered_cached_candidates(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    cache.replace_logo_source_entries(
        source="unregistered",
        chain_id=1,
        entries=[
            TokenLogoSourceEntry(
                source="unregistered",
                chain_id=1,
                address=USDC,
                logo_url="https://attacker.example/logo.png",
            )
        ],
        synced_at=1,
    )
    candidates = TokenLogoSourceManager(cache=cache).get_candidates(chain_id=1, address=USDC)
    assert all(candidate.source != "unregistered" for candidate in candidates)
    assert all("attacker.example" not in candidate.url for candidate in candidates)


@respx.mock
@pytest.mark.asyncio
async def test_fixed_metadata_endpoint_refreshes_source_cache(tmp_path: Path) -> None:
    source = CoinGeckoSource()
    assert source.metadata_url is not None
    route = respx.get(source.metadata_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "tokens": [
                    {
                        "chainId": 1,
                        "address": USDC,
                        "logoURI": (
                            "https://assets.coingecko.com/coins/images/6319/thumb/usdc.png"
                        ),
                    }
                ]
            },
        )
    )
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(source,))

    result = await manager.refresh_sources(
        chain_ids=[1],
        refresh_interval_ms=12 * 60 * 60 * 1000,
        force=True,
    )

    assert route.called
    assert result == {1: {"coingecko": 1}}
    candidates = manager.get_candidates(chain_id=1, address=USDC)
    assert [candidate.source for candidate in candidates] == ["coingecko"]
    assert candidates[0].url.startswith("https://assets.coingecko.com/coins/images/")


@respx.mock
@pytest.mark.asyncio
async def test_metadata_refresh_rejects_an_oversized_body_before_reading_it(
    tmp_path: Path,
) -> None:
    source = CoinGeckoSource()
    assert source.metadata_url is not None
    respx.get(source.metadata_url).mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Length": str(8 * 1024 * 1024 + 1)},
            content=b"{}",
        )
    )
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(source,))

    result = await manager.refresh_sources(
        chain_ids=[1],
        refresh_interval_ms=12 * 60 * 60 * 1000,
        force=True,
    )

    assert result == {}
    assert cache.get_logo_source_sync_state(source=source.id, chain_id=1) is None

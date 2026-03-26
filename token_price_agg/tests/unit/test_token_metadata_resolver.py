from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

import pytest

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import PriceResult, TokenMetadata, TokenRef
from token_price_agg.token_metadata.resolver import TokenMetadataResolver

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.mark.asyncio
async def test_resolver_returns_provider_logo_ephemerally_for_unknown(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)

    request_token = TokenRef(chain_id=1, address=USDC)
    provider_token = TokenRef(
        chain_id=1,
        address=USDC,
        symbol="USDC",
        decimals=6,
        logo_url="https://example.com/usdc.png",
    )
    result = PriceResult(
        provider="defillama",
        status=ProviderStatus.OK,
        token=provider_token,
        price_usd=Decimal("1"),
        latency_ms=10,
    )

    first = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=request_token,
        results=[result],
    )
    # Provider logo returned ephemerally in response
    assert first[USDC].symbol == "USDC"
    assert first[USDC].decimals == 6
    assert first[USDC].logo_url == "https://example.com/usdc.png"
    assert first[USDC].logo_status == "unknown"

    # But NOT persisted to cache
    cached = resolver._cache.get_many(chain_id=1, addresses=[USDC])
    assert cached[USDC].logo_url is None


@pytest.mark.asyncio
async def test_resolver_does_not_return_static_fallbacks_for_unknown(
    tmp_path: Path,
) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )
    # No provider logo, no verified cache — returns None, not a static fallback
    assert metadata[USDC].logo_url is None
    assert metadata[USDC].logo_status == "unknown"


@pytest.mark.asyncio
async def test_resolver_returns_null_logo_for_known_invalid_cached_token(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url=None,
                logo_status="invalid",
                logo_checked_at=int(time.time()),
                logo_http_status=404,
            )
        ]
    )

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )

    assert metadata[USDC].logo_url is None
    assert metadata[USDC].logo_status == "invalid"


@pytest.mark.asyncio
async def test_resolver_retries_invalid_logo_when_new_sources_sync(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url=None,
                logo_status="invalid",
                logo_checked_at=100,
                logo_http_status=404,
            )
        ]
    )
    resolver._cache.upsert_logo_source_sync_state(
        source="coingecko",
        chain_id=1,
        synced_at=200,
    )

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )

    assert metadata[USDC].logo_url is None
    assert metadata[USDC].logo_status == "unknown"


@pytest.mark.asyncio
async def test_resolver_uses_cached_logo_for_known_valid_cached_token(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url="https://example.com/verified-usdc.png",
                logo_status="valid",
                logo_source="provider",
                logo_checked_at=int(time.time()),
                logo_http_status=200,
            )
        ]
    )

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )

    assert metadata[USDC].logo_url == "https://example.com/verified-usdc.png"
    assert metadata[USDC].logo_status == "valid"
    assert metadata[USDC].logo_source == "provider"


@pytest.mark.asyncio
async def test_resolver_treats_stale_valid_as_unknown(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url="https://example.com/old-usdc.png",
                logo_status="valid",
                logo_source="smoldapp",
                logo_checked_at=int(time.time()) - 30 * 86400,  # 30 days old
                logo_http_status=200,
            )
        ]
    )

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )

    # Stale valid is treated as unknown — no static fallback returned
    assert metadata[USDC].logo_url is None
    assert metadata[USDC].logo_status == "unknown"


@pytest.mark.asyncio
async def test_resolver_uses_onchain_multicall_when_provider_has_no_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)

    calls: list[list[str]] = []

    async def _mock_fetch_onchain_metadata(
        *, chain_id: int, addresses: list[str]
    ) -> dict[str, TokenMetadata]:
        calls.append(addresses)
        return {
            USDC: TokenMetadata(
                chain_id=chain_id,
                address=USDC,
                symbol="USDC",
                decimals=6,
                source="onchain_multicall",
            )
        }

    monkeypatch.setattr(resolver, "_fetch_onchain_metadata", _mock_fetch_onchain_metadata)

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )

    assert calls == [[USDC]]
    assert metadata[USDC].symbol == "USDC"
    assert metadata[USDC].decimals == 6


@pytest.mark.asyncio
async def test_resolver_preserves_multiple_provider_logo_urls(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)

    provider_a = TokenRef(
        chain_id=1,
        address=USDC,
        symbol="USDC",
        decimals=6,
        logo_url="https://provider-a.com/usdc.png",
    )
    provider_b = TokenRef(
        chain_id=1,
        address=USDC,
        symbol="USDC",
        decimals=6,
        logo_url="https://provider-b.com/usdc.png",
    )
    results = [
        PriceResult(
            provider="lifi",
            status=ProviderStatus.OK,
            token=provider_a,
            price_usd=Decimal("1"),
            latency_ms=10,
        ),
        PriceResult(
            provider="defillama",
            status=ProviderStatus.OK,
            token=provider_b,
            price_usd=Decimal("1"),
            latency_ms=10,
        ),
    ]

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=results,
    )

    # First provider logo returned ephemerally
    assert metadata[USDC].logo_url == "https://provider-a.com/usdc.png"

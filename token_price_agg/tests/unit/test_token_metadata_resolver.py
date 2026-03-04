from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import PriceResult, TokenMetadata, TokenRef
from token_price_agg.token_metadata.resolver import TokenMetadataResolver

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.mark.asyncio
async def test_resolver_uses_provider_metadata_and_persists_cache(tmp_path: Path) -> None:
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
    assert first[USDC].symbol == "USDC"
    assert first[USDC].decimals == 6
    assert first[USDC].logo_url == "https://example.com/usdc.png"

    second = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=request_token,
        results=[],
    )
    assert second[USDC].symbol == "USDC"
    assert second[USDC].decimals == 6
    assert second[USDC].logo_url == "https://example.com/usdc.png"


@pytest.mark.asyncio
async def test_resolver_applies_smoldapp_logo_fallback(
    tmp_path: Path,
) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)

    metadata = await resolver.resolve_from_price_results(
        chain_id=1,
        request_token=TokenRef(chain_id=1, address=USDC),
        results=[],
    )
    assert metadata[USDC].logo_url == f"https://assets.smold.app/api/token/1/{USDC.lower()}/logo-128.png"


@pytest.mark.asyncio
async def test_resolver_returns_null_logo_for_known_invalid_cached_token(tmp_path: Path) -> None:
    settings = Settings(token_metadata_db_path=str(tmp_path / "token_cache.sqlite3"), rpc_urls=[])
    resolver = TokenMetadataResolver(settings)
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url="https://assets.smold.app/api/token/1/invalid/logo-128.png",
                logo_status="invalid",
                logo_checked_at=1_700_000_000,
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
                logo_checked_at=1_700_000_000,
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

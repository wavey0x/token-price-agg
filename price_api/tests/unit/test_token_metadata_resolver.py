from __future__ import annotations

import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from price_api.app.config import Settings
from price_api.core.errors import ProviderStatus
from price_api.core.models import PriceResult, QuoteResult, TokenMetadata, TokenRef
from price_api.core.validator import NATIVE_TOKEN_ALIAS
from price_api.token_metadata.logo_urls import token_logo_url
from price_api.token_metadata.resolver import TokenMetadataResolver

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        token_metadata_db_path=str(tmp_path / "metadata.sqlite3"),
        rpc_urls=[],
    )


@pytest.mark.asyncio
async def test_provider_logo_urls_are_ignored_and_resource_url_is_deterministic(
    tmp_path: Path,
) -> None:
    resolver = TokenMetadataResolver(settings(tmp_path))
    try:
        metadata = await resolver.resolve_token(
            chain_id=1,
            request_token=TokenRef(
                chain_id=1,
                address=USDC.lower(),
                symbol="USDC",
                decimals=6,
                logo_url="https://provider.example/expiring.png",
            ),
        )
    finally:
        await resolver.aclose()

    assert metadata[USDC].logo_url == token_logo_url(chain_id=1, address=USDC)
    cached = resolver.cache.get_many(chain_id=1, addresses=[USDC])
    assert cached[USDC].logo_url is None
    with closing(sqlite3.connect(resolver.cache.db_path)) as conn:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='token_metadata'"
        ).fetchone()[0]
    assert "logo_url" not in schema
    assert "provider.example" not in resolver.cache.db_path.read_bytes().decode(
        "latin-1",
        errors="ignore",
    )


@pytest.mark.asyncio
async def test_resolution_enrolls_one_canonical_identity_case_insensitively(
    tmp_path: Path,
) -> None:
    resolver = TokenMetadataResolver(settings(tmp_path))
    try:
        await resolver._resolve(
            chain_id=1,
            refs=[
                TokenRef(chain_id=1, address=USDC.lower()),
                TokenRef(chain_id=1, address=USDC.upper().replace("0X", "0x")),
            ],
            source="test",
        )
    finally:
        await resolver.aclose()

    with closing(sqlite3.connect(resolver.cache.db_path)) as conn:
        rows = conn.execute("SELECT chain_id, address FROM token_logos").fetchall()
    assert rows == [(1, USDC)]


@pytest.mark.asyncio
async def test_cached_metadata_is_merged_without_storing_logo_presentation(
    tmp_path: Path,
) -> None:
    resolver = TokenMetadataResolver(settings(tmp_path))
    resolver.cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                symbol="USDC",
                decimals=6,
                logo_url="https://must-not-be-persisted.example/logo.png",
                source="seed",
            )
        ]
    )
    try:
        metadata = await resolver.resolve_token(
            chain_id=1,
            request_token=TokenRef(chain_id=1, address=USDC),
        )
    finally:
        await resolver.aclose()

    assert metadata[USDC].symbol == "USDC"
    assert metadata[USDC].decimals == 6
    assert metadata[USDC].logo_url == token_logo_url(chain_id=1, address=USDC)
    assert resolver.cache.get_many(chain_id=1, addresses=[USDC])[USDC].logo_url is None


@pytest.mark.asyncio
async def test_price_results_enroll_request_and_underlying_identities(tmp_path: Path) -> None:
    resolver = TokenMetadataResolver(settings(tmp_path))
    result = PriceResult(
        provider="test",
        status=ProviderStatus.OK,
        token=TokenRef(chain_id=1, address=DAI),
        price_usd=Decimal(1),
        latency_ms=1,
    )
    try:
        metadata = await resolver.resolve_from_price_results(
            chain_id=1,
            request_token=TokenRef(chain_id=1, address=USDC),
            results=[result],
        )
    finally:
        await resolver.aclose()

    assert metadata[USDC].logo_url == token_logo_url(chain_id=1, address=USDC)
    assert metadata[DAI].logo_url == token_logo_url(chain_id=1, address=DAI)
    with closing(sqlite3.connect(resolver.cache.db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM token_logos").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_quote_results_never_surface_provider_image_urls(tmp_path: Path) -> None:
    resolver = TokenMetadataResolver(settings(tmp_path))
    token_in = TokenRef(
        chain_id=1,
        address=USDC,
        logo_url="https://provider-a.example/usdc.png",
    )
    token_out = TokenRef(
        chain_id=1,
        address=DAI,
        logo_url="https://provider-b.example/dai.png",
    )
    result = QuoteResult(
        provider="test",
        status=ProviderStatus.OK,
        token_in=token_in,
        token_out=token_out,
        amount_in=1,
        amount_out=1,
        latency_ms=1,
    )
    try:
        metadata = await resolver.resolve_from_quote_results(
            chain_id=1,
            request_token_in=token_in,
            request_token_out=token_out,
            results=[result],
        )
    finally:
        await resolver.aclose()

    assert metadata[USDC].logo_url == token_logo_url(chain_id=1, address=USDC)
    assert metadata[DAI].logo_url == token_logo_url(chain_id=1, address=DAI)


@pytest.mark.asyncio
async def test_native_alias_has_one_canonical_logo_identity(tmp_path: Path) -> None:
    resolver = TokenMetadataResolver(settings(tmp_path))
    try:
        metadata = await resolver.resolve_token(
            chain_id=1,
            request_token=TokenRef(
                chain_id=1,
                address="0x0000000000000000000000000000000000000000",
            ),
        )
    finally:
        await resolver.aclose()

    assert metadata[NATIVE_TOKEN_ALIAS].symbol == "ETH"
    assert metadata[NATIVE_TOKEN_ALIAS].decimals == 18
    assert metadata[NATIVE_TOKEN_ALIAS].logo_url == token_logo_url(
        chain_id=1,
        address=NATIVE_TOKEN_ALIAS,
    )

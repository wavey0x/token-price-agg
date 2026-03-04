from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import ClassVar, cast

import pytest

from token_price_agg.app.config import Settings
from token_price_agg.core.aggregator import AggregatorService
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
    TokenRef,
)
from token_price_agg.providers.base import ProviderPlugin
from token_price_agg.providers.registry import ProviderRegistry
from token_price_agg.vault.resolver import VaultResolver

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
CRV = "0xD533a949740bb3306d119CC777fa900bA034cd52"


class StubRegistry:
    def __init__(self, plugin: ProviderPlugin) -> None:
        self._plugin = plugin

    def resolve(self, **_: object) -> list[ProviderPlugin]:
        return [self._plugin]


class StubVaultResolver:
    async def resolve_price_request(
        self,
        req: ProviderPriceRequest,
    ) -> tuple[ProviderPriceRequest, None]:
        return req, None

    async def resolve_quote_request(
        self,
        req: ProviderQuoteRequest,
    ) -> tuple[ProviderQuoteRequest, None]:
        return req, None


class DummyPlugin(ProviderPlugin):
    id: ClassVar[str] = "dummy"
    supports_price: ClassVar[bool] = True
    supports_quote: ClassVar[bool] = True
    supported_chains: ClassVar[list[int]] = [1]

    def __init__(
        self,
        *,
        available: bool = True,
        unavailable_reason: str | None = None,
        price_impl: Callable[[ProviderPriceRequest], Awaitable[PriceResult]] | None = None,
        quote_impl: Callable[[ProviderQuoteRequest], Awaitable[QuoteResult]] | None = None,
    ) -> None:
        super().__init__(available=available, unavailable_reason=unavailable_reason)
        self._price_impl = price_impl
        self._quote_impl = quote_impl

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        if self._price_impl is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=10,
            )
        return await self._price_impl(req)

    async def get_quote(self, req: ProviderQuoteRequest) -> QuoteResult:
        if self._quote_impl is None:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                amount_out=req.amount_in,
                latency_ms=10,
            )
        return await self._quote_impl(req)


class QuoteOnlyDummyPlugin(DummyPlugin):
    supports_price: ClassVar[bool] = False


@pytest.fixture
def price_request() -> ProviderPriceRequest:
    return ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))


@pytest.fixture
def quote_request() -> ProviderQuoteRequest:
    return ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address=CRV),
        token_out=TokenRef(chain_id=1, address=USDC),
        amount_in=10**18,
    )


def _build_service(plugin: ProviderPlugin, *, request_timeout_ms: int = 500) -> AggregatorService:
    settings = Settings(
        providers_enabled=[plugin.id],
        provider_request_timeout_ms=request_timeout_ms,
        provider_fanout_per_request=2,
        provider_global_limit=2,
    )
    return AggregatorService(
        settings=settings,
        registry=cast(ProviderRegistry, StubRegistry(plugin)),
        vault_resolver=cast(VaultResolver, StubVaultResolver()),
    )


@pytest.mark.asyncio
async def test_aggregate_prices_unsupported_operation_returns_provider_failure(
    price_request: ProviderPriceRequest,
) -> None:
    plugin = QuoteOnlyDummyPlugin()
    service = _build_service(plugin)

    results, summary, partial = await service.aggregate_prices(
        req=price_request,
        provider_ids=[plugin.id],
        is_vault=False,
    )

    assert summary.requested_providers == 1
    assert summary.failed_providers == 1
    assert partial is True
    assert results[0].status == ProviderStatus.INVALID_REQUEST
    assert results[0].error is not None
    assert results[0].error.code == "UNSUPPORTED_OPERATION"


@pytest.mark.asyncio
async def test_aggregate_prices_unavailable_provider_returns_provider_failure(
    price_request: ProviderPriceRequest,
) -> None:
    plugin = DummyPlugin(available=False, unavailable_reason="missing_api_key")
    service = _build_service(plugin)

    results, summary, partial = await service.aggregate_prices(
        req=price_request,
        provider_ids=[plugin.id],
        is_vault=False,
    )

    assert summary.requested_providers == 1
    assert summary.failed_providers == 1
    assert partial is True
    assert results[0].status == ProviderStatus.INVALID_REQUEST
    assert results[0].error is not None
    assert results[0].error.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_aggregate_prices_plugin_exception_returns_internal_error(
    price_request: ProviderPriceRequest,
) -> None:
    async def _boom(_: ProviderPriceRequest) -> PriceResult:
        raise RuntimeError("boom")

    plugin = DummyPlugin(price_impl=_boom)
    service = _build_service(plugin)

    results, summary, partial = await service.aggregate_prices(
        req=price_request,
        provider_ids=[plugin.id],
        is_vault=False,
    )

    assert summary.requested_providers == 1
    assert summary.failed_providers == 1
    assert partial is True
    assert results[0].status == ProviderStatus.INTERNAL_ERROR
    assert results[0].error is not None
    assert results[0].error.code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_aggregate_quotes_deadline_timeout_returns_timeout_result(
    quote_request: ProviderQuoteRequest,
) -> None:
    async def _slow(_: ProviderQuoteRequest) -> QuoteResult:
        await asyncio.sleep(1)
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=quote_request.token_in,
            token_out=quote_request.token_out,
            amount_in=quote_request.amount_in,
            amount_out=1,
            latency_ms=1000,
        )

    plugin = DummyPlugin(quote_impl=_slow)
    service = _build_service(plugin, request_timeout_ms=50)

    started = time.perf_counter()
    results, summary, partial = await service.aggregate_quotes(
        req=quote_request,
        provider_ids=[plugin.id],
        is_vault=False,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.7
    assert summary.requested_providers == 1
    assert summary.failed_providers == 1
    assert partial is True
    assert results[0].status == ProviderStatus.TIMEOUT
    assert results[0].error is not None
    assert results[0].error.code == "DEADLINE_EXCEEDED"

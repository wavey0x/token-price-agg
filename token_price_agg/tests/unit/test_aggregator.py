from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import ClassVar, cast

import pytest

from token_price_agg.app.config import Settings
from token_price_agg.core.aggregator import AggregatorService
from token_price_agg.core.errors import InvalidRequestError, ProviderStatus
from token_price_agg.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
    TokenRef,
    VaultContext,
    VaultType,
)
from token_price_agg.providers.base import ProviderPlugin
from token_price_agg.providers.registry import ProviderRegistry
from token_price_agg.vault.resolver import QuoteVaultResolution, VaultResolver

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
    ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution | None]:
        return req, None


class StubVaultResolverWithContext:
    async def resolve_price_request(
        self,
        req: ProviderPriceRequest,
    ) -> tuple[ProviderPriceRequest, VaultContext]:
        return (
            req,
            VaultContext(
                vault_type=VaultType.ERC4626,
                underlying_token=USDC,
                price_per_share=Decimal("1.5"),
                block_number=123,
            ),
        )

    async def resolve_quote_request(
        self,
        req: ProviderQuoteRequest,
    ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution | None]:
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


def _build_vault_service(plugin: ProviderPlugin) -> AggregatorService:
    settings = Settings(
        providers_enabled=[plugin.id],
        provider_request_timeout_ms=500,
        provider_fanout_per_request=2,
        provider_global_limit=2,
    )
    return AggregatorService(
        settings=settings,
        registry=cast(ProviderRegistry, StubRegistry(plugin)),
        vault_resolver=cast(VaultResolver, StubVaultResolverWithContext()),
    )


def _build_service_with_resolver(
    plugin: ProviderPlugin, *, resolver: object, request_timeout_ms: int = 500
) -> AggregatorService:
    settings = Settings(
        providers_enabled=[plugin.id],
        provider_request_timeout_ms=request_timeout_ms,
        provider_fanout_per_request=2,
        provider_global_limit=2,
    )
    return AggregatorService(
        settings=settings,
        registry=cast(ProviderRegistry, StubRegistry(plugin)),
        vault_resolver=cast(VaultResolver, resolver),
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
        use_underlying=False,
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
        use_underlying=False,
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
        use_underlying=False,
    )

    assert summary.requested_providers == 1
    assert summary.failed_providers == 1
    assert partial is True
    assert results[0].status == ProviderStatus.INTERNAL_ERROR
    assert results[0].error is not None
    assert results[0].error.code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_aggregate_prices_applies_vault_share_to_asset_rate(
    price_request: ProviderPriceRequest,
) -> None:
    async def _fixed_price(req: ProviderPriceRequest) -> PriceResult:
        return PriceResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token=req.token,
            price_usd=Decimal("10"),
            latency_ms=10,
        )

    plugin = DummyPlugin(price_impl=_fixed_price)
    service = _build_vault_service(plugin)

    results, summary, partial = await service.aggregate_prices(
        req=price_request,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is False
    assert results[0].price_usd == Decimal("15")
    assert summary.high_price == Decimal("15")
    assert results[0].vault_context is not None
    assert results[0].vault_context.price_per_share == Decimal("1.5")


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
        use_underlying=False,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.7
    assert summary.requested_providers == 1
    assert summary.failed_providers == 1
    assert partial is True
    assert results[0].status == ProviderStatus.TIMEOUT
    assert results[0].error is not None
    assert results[0].error.code == "DEADLINE_EXCEEDED"


@pytest.mark.asyncio
async def test_aggregate_quotes_applies_underlying_for_both_legs() -> None:
    input_vault = TokenRef(chain_id=1, address="0x13db1cb418573f4c3a2ea36486f0e421bc0d2427")
    input_underlying = TokenRef(chain_id=1, address=CRV)
    output_vault = TokenRef(chain_id=1, address="0x5f18c75abdae578b483e5f43f12a39cf75b973a9")
    output_underlying = TokenRef(chain_id=1, address=USDC)
    original_amount_in = 10**18
    converted_amount_in = 3 * 10**18 // 2

    class QuoteVaultResolverStub:
        async def resolve_price_request(
            self,
            req: ProviderPriceRequest,
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            return req, VaultContext(
                vault_type=VaultType.ERC4626,
                underlying_token=req.token.address,
                price_per_share=Decimal("1"),
                block_number=1,
            )

        async def resolve_quote_request(
            self,
            req: ProviderQuoteRequest,
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution]:
            assert req.token_in.address == input_vault.address
            assert req.token_out.address == output_vault.address
            assert req.amount_in == original_amount_in

            converted = ProviderQuoteRequest(
                chain_id=req.chain_id,
                token_in=input_underlying,
                token_out=output_underlying,
                amount_in=converted_amount_in,
            )
            resolution = QuoteVaultResolution(
                input_vault_context=VaultContext(
                    vault_type=VaultType.ERC4626,
                    underlying_token=input_underlying.address,
                    price_per_share=Decimal("1.5"),
                    block_number=123,
                ),
                output_vault_context=VaultContext(
                    vault_type=VaultType.YEARN_V2,
                    underlying_token=output_underlying.address,
                    price_per_share=Decimal("2"),
                    block_number=123,
                ),
                output_assets_to_shares=lambda assets: assets // 2,
            )
            return converted, resolution

    async def _quote_impl(req: ProviderQuoteRequest) -> QuoteResult:
        assert req.token_in.address == input_underlying.address
        assert req.token_out.address == output_underlying.address
        assert req.amount_in == converted_amount_in
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=2_000,
            amount_out_min=1_980,
            latency_ms=10,
        )

    plugin = DummyPlugin(quote_impl=_quote_impl)
    service = _build_service_with_resolver(plugin, resolver=QuoteVaultResolverStub())

    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=input_vault,
        token_out=output_vault,
        amount_in=original_amount_in,
    )
    results, summary, partial = await service.aggregate_quotes(
        req=req,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is False
    assert summary.requested_providers == 1
    assert results[0].amount_in == original_amount_in
    assert results[0].amount_out == 1_000
    assert results[0].amount_out_min == 990
    assert results[0].vault_context is not None
    assert results[0].vault_context.underlying_token is None
    assert results[0].vault_context.underlying_token_in == input_underlying.address
    assert results[0].vault_context.underlying_token_out == output_underlying.address
    assert results[0].vault_context.price_per_share is None
    assert results[0].vault_context.price_per_share_token_in == Decimal("1.5")
    assert results[0].vault_context.price_per_share_token_out == Decimal("2")


@pytest.mark.asyncio
async def test_aggregate_prices_use_underlying_is_best_effort_on_resolution_failure(
    price_request: ProviderPriceRequest,
) -> None:
    class FailingVaultResolver:
        async def resolve_price_request(
            self,
            req: ProviderPriceRequest,
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            raise InvalidRequestError("RPC_NOT_CONFIGURED", "Vault resolution requires RPC_URLS")

        async def resolve_quote_request(
            self,
            req: ProviderQuoteRequest,
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution | None]:
            return req, None

    async def _price_impl(req: ProviderPriceRequest) -> PriceResult:
        assert req.token.address == price_request.token.address
        return PriceResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token=req.token,
            price_usd=Decimal("1"),
            latency_ms=10,
        )

    plugin = DummyPlugin(price_impl=_price_impl)
    service = _build_service_with_resolver(plugin, resolver=FailingVaultResolver())
    results, summary, partial = await service.aggregate_prices(
        req=price_request,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is False
    assert summary.successful_providers == 1
    assert results[0].price_usd == Decimal("1")
    assert results[0].vault_context is None


@pytest.mark.asyncio
async def test_aggregate_quotes_use_underlying_is_best_effort_on_resolution_failure(
    quote_request: ProviderQuoteRequest,
) -> None:
    class FailingVaultResolver:
        async def resolve_price_request(
            self,
            req: ProviderPriceRequest,
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            return req, VaultContext(
                vault_type=VaultType.ERC4626,
                underlying_token=req.token.address,
                price_per_share=Decimal("1"),
                block_number=1,
            )

        async def resolve_quote_request(
            self,
            req: ProviderQuoteRequest,
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution]:
            raise InvalidRequestError("INVALID_VAULT", "Token is not a supported vault")

    async def _quote_impl(req: ProviderQuoteRequest) -> QuoteResult:
        assert req.token_in.address == quote_request.token_in.address
        assert req.token_out.address == quote_request.token_out.address
        assert req.amount_in == quote_request.amount_in
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=123,
            latency_ms=10,
        )

    plugin = DummyPlugin(quote_impl=_quote_impl)
    service = _build_service_with_resolver(plugin, resolver=FailingVaultResolver())
    results, summary, partial = await service.aggregate_quotes(
        req=quote_request,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is False
    assert summary.successful_providers == 1
    assert results[0].amount_out == 123
    assert results[0].vault_context is None


@pytest.mark.asyncio
async def test_aggregate_quotes_use_underlying_false_does_not_apply_vault_conversion(
    quote_request: ProviderQuoteRequest,
) -> None:
    class ExplodingResolver:
        async def resolve_price_request(
            self, req: ProviderPriceRequest
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            raise AssertionError("should not be called")

        async def resolve_quote_request(
            self, req: ProviderQuoteRequest
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution]:
            raise AssertionError("should not be called")

    async def _quote_impl(req: ProviderQuoteRequest) -> QuoteResult:
        assert req.amount_in == quote_request.amount_in
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=777,
            amount_out_min=700,
            latency_ms=10,
        )

    plugin = DummyPlugin(quote_impl=_quote_impl)
    service = _build_service_with_resolver(plugin, resolver=ExplodingResolver())
    results, summary, partial = await service.aggregate_quotes(
        req=quote_request,
        provider_ids=[plugin.id],
        use_underlying=False,
    )

    assert partial is False
    assert summary.successful_providers == 1
    assert results[0].amount_in == quote_request.amount_in
    assert results[0].amount_out == 777
    assert results[0].amount_out_min == 700


@pytest.mark.asyncio
async def test_aggregate_quotes_output_vault_only_converts_amounts_back_to_shares(
    quote_request: ProviderQuoteRequest,
) -> None:
    class OutputVaultResolver:
        async def resolve_price_request(
            self, req: ProviderPriceRequest
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            return req, VaultContext(
                vault_type=VaultType.ERC4626,
                underlying_token=req.token.address,
                price_per_share=Decimal("1"),
                block_number=1,
            )

        async def resolve_quote_request(
            self, req: ProviderQuoteRequest
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution]:
            converted = ProviderQuoteRequest(
                chain_id=req.chain_id,
                token_in=req.token_in,
                token_out=TokenRef(chain_id=req.chain_id, address=USDC),
                amount_in=req.amount_in,
            )
            return converted, QuoteVaultResolution(
                input_vault_context=None,
                output_vault_context=VaultContext(
                    vault_type=VaultType.YEARN_V2,
                    underlying_token=USDC,
                    price_per_share=Decimal("2"),
                    block_number=123,
                ),
                output_assets_to_shares=lambda assets: assets // 2,
            )

    async def _quote_impl(req: ProviderQuoteRequest) -> QuoteResult:
        # Provider quoted underlying output units; aggregator must convert back to shares.
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=2_222,
            amount_out_min=2_000,
            latency_ms=10,
        )

    plugin = DummyPlugin(quote_impl=_quote_impl)
    service = _build_service_with_resolver(plugin, resolver=OutputVaultResolver())
    results, summary, partial = await service.aggregate_quotes(
        req=quote_request,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is False
    assert summary.successful_providers == 1
    assert results[0].amount_in == quote_request.amount_in
    assert results[0].amount_out == 1_111
    assert results[0].amount_out_min == 1_000
    assert results[0].vault_context is not None
    assert results[0].vault_context.underlying_token_in is None
    assert results[0].vault_context.underlying_token_out == USDC
    assert results[0].vault_context.price_per_share is None
    assert results[0].vault_context.price_per_share_token_in is None
    assert results[0].vault_context.price_per_share_token_out == Decimal("2")


@pytest.mark.asyncio
async def test_aggregate_quotes_uses_exact_output_assets_to_shares_converter_when_provided(
    quote_request: ProviderQuoteRequest,
) -> None:
    class OutputVaultResolver:
        async def resolve_price_request(
            self, req: ProviderPriceRequest
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            return req, VaultContext(
                vault_type=VaultType.ERC4626,
                underlying_token=req.token.address,
                price_per_share=Decimal("1"),
                block_number=1,
            )

        async def resolve_quote_request(
            self, req: ProviderQuoteRequest
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution]:
            converted = ProviderQuoteRequest(
                chain_id=req.chain_id,
                token_in=req.token_in,
                token_out=TokenRef(chain_id=req.chain_id, address=USDC),
                amount_in=req.amount_in,
            )
            return converted, QuoteVaultResolution(
                input_vault_context=None,
                output_vault_context=VaultContext(
                    vault_type=VaultType.ERC4626,
                    underlying_token=USDC,
                    # Intentionally misleading fallback rate; exact converter must win.
                    price_per_share=Decimal("1.098367"),
                    block_number=123,
                ),
                output_assets_to_shares=lambda assets: assets * (10**12),
            )

    async def _quote_impl(req: ProviderQuoteRequest) -> QuoteResult:
        # Provider quoted USDC underlying (6 decimals); should map to 18-decimal shares.
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=900_000,
            amount_out_min=890_000,
            latency_ms=10,
        )

    plugin = DummyPlugin(quote_impl=_quote_impl)
    service = _build_service_with_resolver(plugin, resolver=OutputVaultResolver())
    results, summary, partial = await service.aggregate_quotes(
        req=quote_request,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is False
    assert summary.successful_providers == 1
    assert results[0].amount_out == 900_000_000_000_000_000
    assert results[0].amount_out_min == 890_000_000_000_000_000


@pytest.mark.asyncio
async def test_aggregate_quotes_missing_output_converter_marks_provider_failed(
    quote_request: ProviderQuoteRequest,
) -> None:
    class OutputVaultResolverMissingConverter:
        async def resolve_price_request(
            self, req: ProviderPriceRequest
        ) -> tuple[ProviderPriceRequest, VaultContext]:
            return req, VaultContext(
                vault_type=VaultType.ERC4626,
                underlying_token=req.token.address,
                price_per_share=Decimal("1"),
                block_number=1,
            )

        async def resolve_quote_request(
            self, req: ProviderQuoteRequest
        ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution]:
            converted = ProviderQuoteRequest(
                chain_id=req.chain_id,
                token_in=req.token_in,
                token_out=TokenRef(chain_id=req.chain_id, address=USDC),
                amount_in=req.amount_in,
            )
            return converted, QuoteVaultResolution(
                input_vault_context=None,
                output_vault_context=VaultContext(
                    vault_type=VaultType.ERC4626,
                    underlying_token=USDC,
                    price_per_share=Decimal("1.098367"),
                    block_number=123,
                ),
                output_assets_to_shares=None,
            )

    async def _quote_impl(req: ProviderQuoteRequest) -> QuoteResult:
        return QuoteResult(
            provider="dummy",
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=900_000,
            amount_out_min=890_000,
            latency_ms=10,
        )

    plugin = DummyPlugin(quote_impl=_quote_impl)
    service = _build_service_with_resolver(plugin, resolver=OutputVaultResolverMissingConverter())
    results, summary, partial = await service.aggregate_quotes(
        req=quote_request,
        provider_ids=[plugin.id],
        use_underlying=True,
    )

    assert partial is True
    assert summary.successful_providers == 0
    assert summary.failed_providers == 1
    assert results[0].status == ProviderStatus.INTERNAL_ERROR
    assert results[0].amount_out is None
    assert results[0].amount_out_min is None
    assert results[0].error is not None
    assert results[0].error.code == "INVALID_VAULT_CONVERSION"

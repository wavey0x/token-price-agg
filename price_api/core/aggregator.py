from __future__ import annotations

import logging

from price_api.app.config import Settings
from price_api.core.errors import InvalidRequestError
from price_api.core.models import (
    AggregatePriceSummary,
    AggregateQuoteSummary,
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from price_api.core.normalizer import (
    build_price_summary,
    build_quote_summary,
    sort_price_results,
    sort_quote_results,
)
from price_api.core.provider_runner import ProviderOperationRunner
from price_api.core.vault_underlying import VaultUnderlyingService
from price_api.providers.registry import Operation, ProviderRegistry
from price_api.vault.resolver import VaultResolver

_LOGGER = logging.getLogger(__name__)


class AggregatorService:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: ProviderRegistry,
        vault_resolver: VaultResolver,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._runner = ProviderOperationRunner(settings=settings)
        self._vault_underlying = VaultUnderlyingService(
            settings=settings,
            vault_resolver=vault_resolver,
        )

    async def aggregate_prices(
        self,
        *,
        req: ProviderPriceRequest,
        provider_ids: list[str] | None,
        use_underlying: bool,
        timeout_ms: int | None = None,
    ) -> tuple[list[PriceResult], AggregatePriceSummary, bool]:
        selected = self._registry.resolve(
            provider_ids=provider_ids,
            operation=Operation.PRICE,
            chain_id=req.chain_id,
        )
        if not selected:
            raise InvalidRequestError("NO_PROVIDERS", "No providers available for this request")

        resolved = await self._vault_underlying.resolve_price(
            req=req,
            use_underlying=use_underlying,
        )
        resolved_req = resolved.request

        effective_timeout = (
            timeout_ms if timeout_ms is not None else self._settings.provider_request_timeout_ms
        )
        price_deadline = effective_timeout + 100

        if timeout_ms is not None:
            resolved_req = resolved_req.model_copy(update={"timeout_ms": timeout_ms})

        price_results = await self._runner.run_prices(
            plugins=selected,
            req=resolved_req,
            deadline_ms=price_deadline,
        )

        self._vault_underlying.apply_price_context(
            req=req,
            results=price_results,
            vault_context=resolved.vault_context,
        )

        ordered = sort_price_results(price_results)
        summary = build_price_summary(ordered)
        partial = summary.failed_providers > 0
        return ordered, summary, partial

    async def aggregate_quotes(
        self,
        *,
        req: ProviderQuoteRequest,
        provider_ids: list[str] | None,
        use_underlying: bool,
        timeout_ms: int | None = None,
    ) -> tuple[list[QuoteResult], AggregateQuoteSummary, bool]:
        selected = self._registry.resolve(
            provider_ids=provider_ids,
            operation=Operation.QUOTE,
            chain_id=req.chain_id,
        )
        if not selected:
            raise InvalidRequestError("NO_PROVIDERS", "No providers available for this request")

        resolved = await self._vault_underlying.resolve_quote(
            req=req,
            use_underlying=use_underlying,
        )
        resolved_req = resolved.request

        effective_timeout = (
            timeout_ms if timeout_ms is not None else self._settings.provider_request_timeout_ms
        )
        quote_deadline = effective_timeout + 300

        if timeout_ms is not None:
            resolved_req = resolved_req.model_copy(update={"timeout_ms": timeout_ms})

        quote_results = await self._runner.run_quotes(
            plugins=selected,
            req=resolved_req,
            deadline_ms=quote_deadline,
        )

        self._vault_underlying.apply_quote_resolution(
            req=req,
            results=quote_results,
            vault_resolution=resolved.vault_resolution,
        )

        ordered = sort_quote_results(quote_results)
        summary = build_quote_summary(ordered)
        partial = summary.failed_providers > 0
        return ordered, summary, partial

    def circuit_open_providers(self) -> set[str]:
        return self._runner.circuit_open_providers()

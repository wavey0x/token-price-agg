from __future__ import annotations

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import InvalidRequestError, ProviderStatus
from token_price_agg.core.models import (
    AggregatePriceSummary,
    AggregateQuoteSummary,
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from token_price_agg.core.normalizer import (
    build_price_summary,
    build_quote_summary,
    sort_price_results,
    sort_quote_results,
)
from token_price_agg.core.provider_runner import ProviderOperationRunner
from token_price_agg.providers.registry import Operation, ProviderRegistry
from token_price_agg.vault.resolver import VaultResolver


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
        self._vault_resolver = vault_resolver
        self._runner = ProviderOperationRunner(settings=settings)

    async def aggregate_prices(
        self,
        *,
        req: ProviderPriceRequest,
        provider_ids: list[str] | None,
        is_vault: bool,
    ) -> tuple[list[PriceResult], AggregatePriceSummary, bool]:
        selected = self._registry.resolve(
            provider_ids=provider_ids,
            operation=Operation.PRICE,
            chain_id=req.chain_id,
        )
        if not selected:
            raise InvalidRequestError("NO_PROVIDERS", "No providers available for this request")

        resolved_req = req
        vault_context = None
        if is_vault:
            resolved_req, vault_context = await self._vault_resolver.resolve_price_request(req)

        price_results = await self._runner.run_prices(
            plugins=selected,
            req=resolved_req,
            deadline_ms=self._settings.aggregate_price_deadline_ms,
        )

        if vault_context is not None:
            for result in price_results:
                if result.status == ProviderStatus.OK:
                    result.vault_context = vault_context

        ordered = sort_price_results(price_results)
        summary = build_price_summary(ordered)
        partial = summary.failed_providers > 0
        return ordered, summary, partial

    async def aggregate_quotes(
        self,
        *,
        req: ProviderQuoteRequest,
        provider_ids: list[str] | None,
        is_vault: bool,
    ) -> tuple[list[QuoteResult], AggregateQuoteSummary, bool]:
        selected = self._registry.resolve(
            provider_ids=provider_ids,
            operation=Operation.QUOTE,
            chain_id=req.chain_id,
        )
        if not selected:
            raise InvalidRequestError("NO_PROVIDERS", "No providers available for this request")

        resolved_req = req
        vault_context = None
        if is_vault:
            resolved_req, vault_context = await self._vault_resolver.resolve_quote_request(req)

        quote_results = await self._runner.run_quotes(
            plugins=selected,
            req=resolved_req,
            deadline_ms=self._settings.aggregate_quote_deadline_ms,
        )

        if vault_context is not None:
            for result in quote_results:
                if result.status == ProviderStatus.OK:
                    result.vault_context = vault_context

        ordered = sort_quote_results(quote_results)
        summary = build_quote_summary(ordered)
        partial = summary.failed_providers > 0
        return ordered, summary, partial

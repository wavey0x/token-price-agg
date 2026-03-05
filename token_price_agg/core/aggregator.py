from __future__ import annotations

from decimal import Decimal, InvalidOperation

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
        use_underlying: bool,
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
        if use_underlying:
            resolved_req, vault_context = await self._vault_resolver.resolve_price_request(req)

        price_results = await self._runner.run_prices(
            plugins=selected,
            req=resolved_req,
            deadline_ms=self._settings.aggregate_price_deadline_ms,
        )

        if vault_context is not None:
            multiplier = _vault_share_to_asset_multiplier(vault_context.share_to_asset_rate)
            for result in price_results:
                if result.status == ProviderStatus.OK:
                    if result.price_usd is not None:
                        result.price_usd = result.price_usd * multiplier
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
        use_underlying: bool,
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
        if use_underlying:
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


def _vault_share_to_asset_multiplier(rate: str) -> Decimal:
    parts = rate.split("/", 1)
    if len(parts) != 2:
        raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault share_to_asset_rate")

    numerator_raw, denominator_raw = parts
    try:
        numerator = Decimal(numerator_raw)
        denominator = Decimal(denominator_raw)
    except InvalidOperation as exc:
        raise InvalidRequestError(
            "INVALID_VAULT_RATE",
            "Invalid vault share_to_asset_rate",
        ) from exc

    if denominator == 0:
        raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault share_to_asset_rate")
    return numerator / denominator

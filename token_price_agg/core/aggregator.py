from __future__ import annotations

import logging
from decimal import Decimal

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import ErrorInfo, InvalidRequestError, ProviderStatus
from token_price_agg.core.models import (
    AggregatePriceSummary,
    AggregateQuoteSummary,
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
    VaultContext,
)
from token_price_agg.core.normalizer import (
    build_price_summary,
    build_quote_summary,
    sort_price_results,
    sort_quote_results,
)
from token_price_agg.core.provider_runner import ProviderOperationRunner
from token_price_agg.providers.registry import Operation, ProviderRegistry
from token_price_agg.vault.resolver import QuoteVaultResolution, VaultResolver

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
            try:
                resolved_req, vault_context = await self._vault_resolver.resolve_price_request(req)
            except Exception:
                # Best effort: if vault resolution fails, continue with original token request.
                _LOGGER.warning(
                    "price_use_underlying_resolution_failed",
                    extra={"chain_id": req.chain_id, "token": req.token.address},
                    exc_info=True,
                )
                resolved_req = req
                vault_context = None

        price_results = await self._runner.run_prices(
            plugins=selected,
            req=resolved_req,
            deadline_ms=self._settings.aggregate_price_deadline_ms,
        )

        if vault_context is not None:
            try:
                multiplier = _vault_share_to_asset_multiplier(vault_context.price_per_share)
            except InvalidRequestError:
                _LOGGER.warning(
                    "price_use_underlying_invalid_rate",
                    extra={"chain_id": req.chain_id, "token": req.token.address},
                    exc_info=True,
                )
                multiplier = None
            for result in price_results:
                if result.status == ProviderStatus.OK and multiplier is not None:
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
        quote_resolution: QuoteVaultResolution | None = None
        if use_underlying:
            try:
                resolved_req, quote_resolution = await self._vault_resolver.resolve_quote_request(
                    req
                )
            except Exception:
                # Best effort: if vault resolution fails, continue with original quote request.
                _LOGGER.warning(
                    "quote_use_underlying_resolution_failed",
                    extra={
                        "chain_id": req.chain_id,
                        "token_in": req.token_in.address,
                        "token_out": req.token_out.address,
                    },
                    exc_info=True,
                )
                resolved_req = req
                quote_resolution = None

        quote_results = await self._runner.run_quotes(
            plugins=selected,
            req=resolved_req,
            deadline_ms=self._settings.aggregate_quote_deadline_ms,
        )

        if quote_resolution is not None:
            input_context = quote_resolution.input_vault_context
            output_context = quote_resolution.output_vault_context
            output_assets_to_shares = quote_resolution.output_assets_to_shares
            missing_output_converter = (
                output_context is not None and output_assets_to_shares is None
            )
            if missing_output_converter:
                _LOGGER.error(
                    "quote_use_underlying_missing_output_converter",
                    extra={
                        "chain_id": req.chain_id,
                        "token_out": req.token_out.address,
                    },
                )
            for result in quote_results:
                if result.status == ProviderStatus.OK:
                    # Keep response amount_in aligned with client request units.
                    result.amount_in = req.amount_in
                    if missing_output_converter:
                        _mark_quote_conversion_failure(result)
                        continue
                    if output_context is not None:
                        assert output_assets_to_shares is not None
                        try:
                            if result.amount_out is not None:
                                result.amount_out = output_assets_to_shares(result.amount_out)
                            if result.amount_out_min is not None:
                                result.amount_out_min = output_assets_to_shares(
                                    result.amount_out_min
                                )
                        except Exception:
                            _LOGGER.warning(
                                "quote_use_underlying_output_conversion_failed",
                                extra={
                                    "chain_id": req.chain_id,
                                    "token_out": req.token_out.address,
                                    "provider": result.provider,
                                },
                                exc_info=True,
                            )
                            _mark_quote_conversion_failure(result)
                            continue
                    result.vault_context = _quote_vault_context(
                        input_context=input_context,
                        output_context=output_context,
                    )

        ordered = sort_quote_results(quote_results)
        summary = build_quote_summary(ordered)
        partial = summary.failed_providers > 0
        return ordered, summary, partial


def _vault_share_to_asset_multiplier(price_per_share: Decimal | None) -> Decimal:
    if price_per_share is None or price_per_share <= 0:
        raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault price_per_share")
    return price_per_share


def _mark_quote_conversion_failure(result: QuoteResult) -> None:
    result.status = ProviderStatus.INTERNAL_ERROR
    result.amount_out = None
    result.amount_out_min = None
    result.error = ErrorInfo(
        code="INVALID_VAULT_CONVERSION",
        message="Failed to convert output amount into vault share base units",
    )


def _quote_vault_context(
    *,
    input_context: VaultContext | None,
    output_context: VaultContext | None,
) -> VaultContext | None:
    if input_context is None and output_context is None:
        return None

    if input_context is not None and output_context is None:
        return input_context.model_copy(
            update={
                "underlying_token": None,
                "underlying_token_in": input_context.underlying_token,
                "underlying_token_out": None,
                "price_per_share": None,
                "price_per_share_token_in": input_context.price_per_share,
                "price_per_share_token_out": None,
            }
        )

    if input_context is None and output_context is not None:
        return output_context.model_copy(
            update={
                "underlying_token": None,
                "underlying_token_in": None,
                "underlying_token_out": output_context.underlying_token,
                "price_per_share": None,
                "price_per_share_token_in": None,
                "price_per_share_token_out": output_context.price_per_share,
            }
        )

    assert input_context is not None and output_context is not None
    return VaultContext(
        vault_type=input_context.vault_type
        if input_context.vault_type == output_context.vault_type
        else None,
        underlying_token=None,
        underlying_token_in=input_context.underlying_token,
        underlying_token_out=output_context.underlying_token,
        price_per_share=None,
        price_per_share_token_in=input_context.price_per_share,
        price_per_share_token_out=output_context.price_per_share,
        block_number=input_context.block_number,
    )

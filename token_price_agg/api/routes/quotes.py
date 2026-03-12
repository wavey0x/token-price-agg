from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from token_price_agg.api.routes.aggregate_utils import (
    aggregate_with_provider_order,
    get_request_id,
    metadata_for_address,
)
from token_price_agg.api.schemas.query_params import parse_provider_query_values
from token_price_agg.api.schemas.requests import QuoteRequest
from token_price_agg.api.schemas.responses import (
    QuoteAggregateResponse,
    QuoteProviderEntry,
    QuoteVaultContext,
    SelectedQuote,
)
from token_price_agg.app.config import MAX_REQUEST_TIMEOUT_MS, MIN_REQUEST_TIMEOUT_MS, Settings, get_settings
from token_price_agg.app.dependencies import get_aggregator_service, get_token_metadata_resolver
from token_price_agg.core.aggregator import AggregatorService
from token_price_agg.core.models import VaultContext
from token_price_agg.core.normalizer import normalize_quote_request
from token_price_agg.core.selection import index_quote_results, select_quote_result
from token_price_agg.token_metadata.resolver import TokenMetadataResolver

router = APIRouter(tags=["quote"])


@router.get("/v1/quote", response_model=QuoteAggregateResponse)
async def quote(
    request: Request,
    token_in: Annotated[str, Query(min_length=42)],
    token_out: Annotated[str, Query(min_length=42)],
    amount_in: Annotated[str, Query()],
    chain_id: Annotated[int, Query(gt=0)] = 1,
    providers: Annotated[list[str] | None, Query()] = None,
    include_route: bool = False,
    use_underlying: Annotated[
        bool,
        Query(
            description=(
                "Best-effort vault resolution for both token_in and token_out. "
                "Supported vault legs are converted to underlying before quoting "
                "and converted back to share units in response. "
                "If vault/web3 resolution fails, request proceeds with original tokens unchanged."
            )
        ),
    ] = False,
    timeout_ms: Annotated[
        int | None,
        Query(
            ge=MIN_REQUEST_TIMEOUT_MS,
            le=MAX_REQUEST_TIMEOUT_MS,
            description=(
                "Per-request provider HTTP timeout in milliseconds. "
                f"Range: {MIN_REQUEST_TIMEOUT_MS}–{MAX_REQUEST_TIMEOUT_MS}. "
                "Overrides the server default when provided."
            ),
        ),
    ] = None,
    aggregator: AggregatorService = Depends(get_aggregator_service),
    token_metadata_resolver: TokenMetadataResolver = Depends(get_token_metadata_resolver),
    settings: Settings = Depends(get_settings),
) -> QuoteAggregateResponse:
    payload = QuoteRequest(
        chain_id=chain_id,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        providers=parse_provider_query_values(providers),
        include_route=include_route,
        use_underlying=use_underlying,
    )
    return await _handle_quote_request(
        request=request,
        payload=payload,
        aggregator=aggregator,
        token_metadata_resolver=token_metadata_resolver,
        settings=settings,
        timeout_ms=timeout_ms,
    )


async def _handle_quote_request(
    *,
    request: Request,
    payload: QuoteRequest,
    aggregator: AggregatorService,
    token_metadata_resolver: TokenMetadataResolver,
    settings: Settings,
    timeout_ms: int | None = None,
) -> QuoteAggregateResponse:
    normalized, original_in, original_out = normalize_quote_request(
        chain_id=payload.chain_id,
        token_in=payload.token_in,
        token_out=payload.token_out,
        amount_in=payload.amount_in,
    )
    response_in = original_in or normalized.token_in
    response_out = original_out or normalized.token_out

    results, summary, provider_order, by_provider = await aggregate_with_provider_order(
        endpoint="/v1/quote",
        aggregate_call=aggregator.aggregate_quotes(
            req=normalized,
            provider_ids=payload.providers,
            use_underlying=payload.use_underlying,
            timeout_ms=timeout_ms,
        ),
        requested_provider_ids=payload.providers,
        default_priority=settings.quote_provider_priority,
        index_results=index_quote_results,
    )

    if not payload.include_route:
        for result in results:
            result.route = None

    request_id = get_request_id(request)
    token_metadata = await token_metadata_resolver.resolve_from_quote_results(
        chain_id=payload.chain_id,
        request_token_in=normalized.token_in,
        request_token_out=normalized.token_out,
        results=results,
    )

    for original, canonical_ref in [
        (original_in, normalized.token_in),
        (original_out, normalized.token_out),
    ]:
        if original is not None:
            canonical_meta = token_metadata.get(canonical_ref.address)
            if canonical_meta is not None:
                token_metadata[original.address] = canonical_meta.model_copy(
                    update={"address": original.address}
                )

    providers_payload: dict[str, QuoteProviderEntry] = {}
    for provider_id in provider_order:
        provider_result = by_provider.get(provider_id)
        if provider_result is None:
            continue
        providers_payload[provider_id] = QuoteProviderEntry(
            status=provider_result.status,
            success=provider_result.success,
            amount_in=provider_result.amount_in,
            amount_out=provider_result.amount_out,
            amount_out_min=provider_result.amount_out_min,
            price_impact_bps=provider_result.price_impact_bps,
            estimated_gas=provider_result.estimated_gas,
            latency_ms=provider_result.latency_ms,
            as_of=provider_result.as_of,
            retrieved_at=provider_result.retrieved_at,
            error=provider_result.error,
            route=provider_result.route,
        )

    selected = select_quote_result(provider_order=provider_order, by_provider=by_provider)
    selected_quote: SelectedQuote | None = None
    if selected is not None:
        selected_quote = SelectedQuote(
            provider=selected.provider,
            amount_in=selected.amount_in,
            amount_out=selected.amount_out,
            amount_out_min=selected.amount_out_min,
            price_impact_bps=selected.price_impact_bps,
            estimated_gas=selected.estimated_gas,
            latency_ms=selected.latency_ms,
            as_of=selected.as_of,
            retrieved_at=selected.retrieved_at,
            route=selected.route,
            vault_context=_to_quote_vault_context(selected.vault_context),
        )

    token_in_meta = metadata_for_address(metadata=token_metadata, token=response_in)
    token_out_meta = metadata_for_address(metadata=token_metadata, token=response_out)

    return QuoteAggregateResponse(
        request_id=request_id,
        chain_id=payload.chain_id,
        token_in=token_in_meta,
        token_out=token_out_meta,
        provider_order=provider_order,
        quote=selected_quote,
        providers=providers_payload,
        summary=summary,
    )


def _to_quote_vault_context(vault_context: VaultContext | None) -> QuoteVaultContext | None:
    if vault_context is None:
        return None
    return QuoteVaultContext(
        vault_type=vault_context.vault_type,
        underlying_token_in=vault_context.underlying_token_in,
        underlying_token_out=vault_context.underlying_token_out,
        price_per_share_token_in=vault_context.price_per_share_token_in,
        price_per_share_token_out=vault_context.price_per_share_token_out,
        block_number=vault_context.block_number,
    )

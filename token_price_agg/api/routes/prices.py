from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from token_price_agg.api.routes.aggregate_utils import (
    aggregate_with_provider_order,
    get_request_id,
    metadata_for_address,
)
from token_price_agg.api.schemas.query_params import parse_provider_query_values
from token_price_agg.api.schemas.requests import PriceRequest
from token_price_agg.api.schemas.responses import (
    PriceAggregateResponse,
    PriceProviderEntry,
    PriceVaultContext,
    SelectedPrice,
)
from token_price_agg.app.config import Settings, get_settings
from token_price_agg.app.dependencies import get_aggregator_service, get_token_metadata_resolver
from token_price_agg.core.aggregator import AggregatorService
from token_price_agg.core.models import VaultContext
from token_price_agg.core.normalizer import normalize_price_request
from token_price_agg.core.selection import index_price_results, select_price_result
from token_price_agg.token_metadata.resolver import TokenMetadataResolver

router = APIRouter(tags=["price"])


@router.get("/v1/price", response_model=PriceAggregateResponse)
async def price(
    request: Request,
    token: Annotated[str, Query(min_length=42)],
    chain_id: Annotated[int, Query(gt=0)] = 1,
    providers: Annotated[list[str] | None, Query()] = None,
    use_underlying: Annotated[
        bool,
        Query(
            description=(
                "Best-effort vault resolution. If token is a supported vault, price is computed "
                "using underlying and converted back to share units. "
                "If vault/web3 resolution fails, "
                "request proceeds with original token unchanged."
            )
        ),
    ] = False,
    aggregator: AggregatorService = Depends(get_aggregator_service),
    token_metadata_resolver: TokenMetadataResolver = Depends(get_token_metadata_resolver),
    settings: Settings = Depends(get_settings),
) -> PriceAggregateResponse:
    payload = PriceRequest(
        chain_id=chain_id,
        token=token,
        providers=parse_provider_query_values(providers),
        use_underlying=use_underlying,
    )
    return await _handle_price_request(
        request=request,
        payload=payload,
        aggregator=aggregator,
        token_metadata_resolver=token_metadata_resolver,
        settings=settings,
    )


async def _handle_price_request(
    *,
    request: Request,
    payload: PriceRequest,
    aggregator: AggregatorService,
    token_metadata_resolver: TokenMetadataResolver,
    settings: Settings,
) -> PriceAggregateResponse:
    normalized = normalize_price_request(
        chain_id=payload.chain_id,
        token=payload.token,
    )
    results, summary, provider_order, by_provider = await aggregate_with_provider_order(
        endpoint="/v1/price",
        aggregate_call=aggregator.aggregate_prices(
            req=normalized,
            provider_ids=payload.providers,
            use_underlying=payload.use_underlying,
        ),
        requested_provider_ids=payload.providers,
        default_priority=settings.price_provider_priority,
        index_results=index_price_results,
    )

    request_id = get_request_id(request)
    token_metadata = await token_metadata_resolver.resolve_from_price_results(
        chain_id=payload.chain_id,
        request_token=normalized.token,
        results=results,
    )
    providers_payload: dict[str, PriceProviderEntry] = {}
    for provider_id in provider_order:
        result = by_provider.get(provider_id)
        if result is None:
            continue
        providers_payload[provider_id] = PriceProviderEntry(
            status=result.status,
            success=result.success,
            price=result.price_usd,
            latency_ms=result.latency_ms,
            as_of=result.as_of,
            retrieved_at=result.retrieved_at,
            error=result.error,
        )

    selected = select_price_result(provider_order=provider_order, by_provider=by_provider)
    selected_price: SelectedPrice | None = None
    if selected is not None:
        selected_price = SelectedPrice(
            provider=selected.provider,
            price=selected.price_usd,
            latency_ms=selected.latency_ms,
            as_of=selected.as_of,
            retrieved_at=selected.retrieved_at,
            vault_context=_to_price_vault_context(selected.vault_context),
        )

    request_token_meta = metadata_for_address(
        metadata=token_metadata,
        token=normalized.token,
    )

    return PriceAggregateResponse(
        request_id=request_id,
        chain_id=payload.chain_id,
        token=request_token_meta,
        provider_order=provider_order,
        price_data=selected_price,
        providers=providers_payload,
        summary=summary,
    )


def _to_price_vault_context(vault_context: VaultContext | None) -> PriceVaultContext | None:
    if vault_context is None or vault_context.price_per_share is None:
        return None
    return PriceVaultContext(
        vault_type=vault_context.vault_type,
        underlying_token=vault_context.underlying_token,
        price_per_share=vault_context.price_per_share,
        block_number=vault_context.block_number,
    )

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Never

from fastapi import HTTPException, Request

from token_price_agg.api.schemas.responses import TokenMetadataResponse
from token_price_agg.core.errors import InvalidRequestError
from token_price_agg.core.models import (
    AggregatePriceSummary,
    AggregateQuoteSummary,
    PriceResult,
    QuoteResult,
    TokenMetadata,
    TokenRef,
)
from token_price_agg.core.selection import build_provider_order
from token_price_agg.observability.metrics import (
    record_all_failed_response,
    record_partial_response,
)

SummaryModel = AggregatePriceSummary | AggregateQuoteSummary
ResultModel = PriceResult | QuoteResult


def raise_bad_request(exc: InvalidRequestError) -> Never:
    raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc


def record_aggregate_metrics(
    *,
    endpoint: str,
    summary: SummaryModel,
    partial: bool,
) -> None:
    if partial:
        record_partial_response(endpoint=endpoint)
    if summary.requested_providers > 0 and summary.failed_providers == summary.requested_providers:
        record_all_failed_response(endpoint=endpoint)


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def metadata_for_address(
    *,
    metadata: dict[str, TokenMetadata],
    token: TokenRef,
) -> TokenMetadataResponse:
    value = metadata.get(token.address)
    if value is None:
        value = TokenMetadata(
            chain_id=token.chain_id,
            address=token.address,
            is_native=token.is_native,
            symbol=token.symbol,
            decimals=token.decimals,
            logo_url=token.logo_url,
            source="fallback",
        )

    return TokenMetadataResponse(
        chain_id=value.chain_id,
        address=value.address,
        is_native=value.is_native,
        symbol=value.symbol,
        decimals=value.decimals,
        logo_url=value.logo_url,
    )


def provider_order_for_results(
    *,
    results: Sequence[ResultModel],
    requested_provider_ids: list[str] | None,
    default_priority: list[str],
) -> list[str]:
    return build_provider_order(
        available_provider_ids=[result.provider for result in results],
        requested_provider_ids=requested_provider_ids,
        default_priority=default_priority,
    )


async def aggregate_with_provider_order[
    TResult: (PriceResult, QuoteResult),
    TSummary: (AggregatePriceSummary, AggregateQuoteSummary),
](
    *,
    endpoint: str,
    aggregate_call: Awaitable[tuple[list[TResult], TSummary, bool]],
    requested_provider_ids: list[str] | None,
    default_priority: list[str],
    index_results: Callable[[list[TResult]], dict[str, TResult]],
) -> tuple[list[TResult], TSummary, list[str], dict[str, TResult]]:
    try:
        results, summary, partial = await aggregate_call
    except InvalidRequestError as exc:
        raise_bad_request(exc)

    record_aggregate_metrics(endpoint=endpoint, summary=summary, partial=partial)

    provider_order = build_provider_order(
        available_provider_ids=[result.provider for result in results],
        requested_provider_ids=requested_provider_ids,
        default_priority=default_priority,
    )
    by_provider = index_results(results)
    return results, summary, provider_order, by_provider

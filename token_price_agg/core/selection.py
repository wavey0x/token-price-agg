from __future__ import annotations

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import PriceResult, QuoteResult


def build_provider_order(
    *,
    available_provider_ids: list[str],
    requested_provider_ids: list[str] | None,
    default_priority: list[str],
) -> list[str]:
    available = set(available_provider_ids)
    ordered: list[str] = []
    seen: set[str] = set()

    if requested_provider_ids is not None:
        for provider_id in requested_provider_ids:
            if provider_id in available and provider_id not in seen:
                ordered.append(provider_id)
                seen.add(provider_id)
    else:
        for provider_id in default_priority:
            if provider_id in available and provider_id not in seen:
                ordered.append(provider_id)
                seen.add(provider_id)

    for provider_id in sorted(available_provider_ids):
        if provider_id not in seen:
            ordered.append(provider_id)
            seen.add(provider_id)

    return ordered


def index_price_results(results: list[PriceResult]) -> dict[str, PriceResult]:
    return {result.provider: result for result in results}


def index_quote_results(results: list[QuoteResult]) -> dict[str, QuoteResult]:
    return {result.provider: result for result in results}


def select_price_result(
    *,
    provider_order: list[str],
    by_provider: dict[str, PriceResult],
) -> PriceResult | None:
    for provider_id in provider_order:
        result = by_provider.get(provider_id)
        if result is None:
            continue
        if result.status == ProviderStatus.OK:
            return result
    return None


def select_quote_result(
    *,
    provider_order: list[str],
    by_provider: dict[str, QuoteResult],
) -> QuoteResult | None:
    for provider_id in provider_order:
        result = by_provider.get(provider_id)
        if result is None:
            continue
        if result.status == ProviderStatus.OK:
            return result
    return None

from __future__ import annotations

from decimal import Decimal
from statistics import median

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import (
    AggregatePriceSummary,
    AggregateQuoteSummary,
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
    TokenRef,
)
from token_price_agg.core.validator import parse_positive_int


def normalize_price_request(*, chain_id: int, token: str) -> ProviderPriceRequest:
    token_ref = TokenRef(chain_id=chain_id, address=token)
    return ProviderPriceRequest(chain_id=chain_id, token=token_ref)


def normalize_quote_request(
    *, chain_id: int, token_in: str, token_out: str, amount_in: str
) -> ProviderQuoteRequest:
    parsed_amount_in = parse_positive_int(amount_in, "amount_in")
    token_in_ref = TokenRef(chain_id=chain_id, address=token_in)
    token_out_ref = TokenRef(chain_id=chain_id, address=token_out)
    return ProviderQuoteRequest(
        chain_id=chain_id,
        token_in=token_in_ref,
        token_out=token_out_ref,
        amount_in=parsed_amount_in,
    )


def _status_rank(status: ProviderStatus) -> int:
    return 0 if status == ProviderStatus.OK else 1


def sort_price_results(results: list[PriceResult]) -> list[PriceResult]:
    return sorted(results, key=lambda item: (_status_rank(item.status), item.provider))


def sort_quote_results(results: list[QuoteResult]) -> list[QuoteResult]:
    return sorted(results, key=lambda item: (_status_rank(item.status), item.provider))


def _deviation_bps(prices: list[Decimal]) -> int | None:
    if len(prices) < 2:
        return None

    low = min(prices)
    high = max(prices)
    if low == 0:
        return None

    spread = (high - low) / low
    return int((spread * Decimal(10000)).to_integral_value())


def build_price_summary(results: list[PriceResult]) -> AggregatePriceSummary:
    successful = [result for result in results if result.status == ProviderStatus.OK]
    prices = [result.price_usd for result in successful if result.price_usd is not None]
    high_price = max(prices) if prices else None
    low_price = min(prices) if prices else None
    best_price = high_price
    median_price = Decimal(str(median(prices))) if prices else None

    return AggregatePriceSummary(
        requested_providers=len(results),
        successful_providers=len(successful),
        failed_providers=len(results) - len(successful),
        best_price=best_price,
        high_price=high_price,
        low_price=low_price,
        median_price=median_price,
        deviation_bps=_deviation_bps(prices) if prices else None,
    )


def build_quote_summary(results: list[QuoteResult]) -> AggregateQuoteSummary:
    successful = [result for result in results if result.status == ProviderStatus.OK]

    best: tuple[str, int] | None = None
    for result in successful:
        if result.amount_out is None:
            continue
        if best is None or result.amount_out > best[1]:
            best = (result.provider, result.amount_out)

    return AggregateQuoteSummary(
        requested_providers=len(results),
        successful_providers=len(successful),
        failed_providers=len(results) - len(successful),
        best_amount_out=best[1] if best is not None else None,
        best_provider=best[0] if best is not None else None,
    )

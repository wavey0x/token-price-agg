from __future__ import annotations

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import QuoteResult
from token_price_agg.core.normalizer import build_quote_summary


def test_build_quote_summary_exposes_high_low_and_median_amount_out() -> None:
    summary = build_quote_summary(
        [
            QuoteResult(provider="a", status=ProviderStatus.OK, amount_out=120, latency_ms=10),
            QuoteResult(provider="b", status=ProviderStatus.OK, amount_out=80, latency_ms=12),
            QuoteResult(provider="c", status=ProviderStatus.OK, amount_out=100, latency_ms=14),
        ]
    )

    assert summary.requested_providers == 3
    assert summary.successful_providers == 3
    assert summary.failed_providers == 0
    assert summary.high_amount_out == 120
    assert summary.low_amount_out == 80
    assert summary.median_amount_out == 100


def test_build_quote_summary_even_count_median_uses_integer_midpoint() -> None:
    summary = build_quote_summary(
        [
            QuoteResult(provider="a", status=ProviderStatus.OK, amount_out=101, latency_ms=10),
            QuoteResult(provider="b", status=ProviderStatus.OK, amount_out=102, latency_ms=12),
            QuoteResult(
                provider="c",
                status=ProviderStatus.INVALID_REQUEST,
                amount_out=None,
                latency_ms=14,
            ),
            QuoteResult(provider="d", status=ProviderStatus.OK, amount_out=105, latency_ms=15),
            QuoteResult(provider="e", status=ProviderStatus.OK, amount_out=106, latency_ms=16),
        ]
    )

    assert summary.requested_providers == 5
    assert summary.successful_providers == 4
    assert summary.failed_providers == 1
    assert summary.high_amount_out == 106
    assert summary.low_amount_out == 101
    assert summary.median_amount_out == 103

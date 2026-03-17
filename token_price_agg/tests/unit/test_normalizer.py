from __future__ import annotations

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import QuoteResult
from token_price_agg.core.normalizer import (
    build_quote_summary,
    normalize_price_request,
    normalize_quote_request,
)


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
                status=ProviderStatus.BAD_REQUEST,
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


# --- normalize_price_request remap tests ---

REMAPPED_ADDR = "0xa3cc91589feedbbee0cfdc7404041e19cb00f110"
CANONICAL_ADDR = "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B"
NORMAL_ADDR = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


def test_normalize_price_no_remap_returns_none_original() -> None:
    req, original = normalize_price_request(chain_id=1, token=NORMAL_ADDR)
    assert original is None
    assert req.token.address == NORMAL_ADDR


def test_normalize_price_with_remap_returns_canonical_and_original() -> None:
    req, original = normalize_price_request(chain_id=1, token=REMAPPED_ADDR)
    assert original is not None
    # original keeps the checksummed form of the input address
    assert original.address.lower() == REMAPPED_ADDR.lower()
    # request uses the canonical (remapped-to) address
    assert req.token.address.lower() == CANONICAL_ADDR.lower()


def test_normalize_quote_independent_remap() -> None:
    req, original_in, original_out = normalize_quote_request(
        chain_id=1,
        token_in=REMAPPED_ADDR,
        token_out=NORMAL_ADDR,
        amount_in="1000",
    )
    # token_in was remapped
    assert original_in is not None
    assert original_in.address.lower() == REMAPPED_ADDR.lower()
    assert req.token_in.address.lower() == CANONICAL_ADDR.lower()
    # token_out was not remapped
    assert original_out is None
    assert req.token_out.address == NORMAL_ADDR

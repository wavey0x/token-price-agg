from __future__ import annotations

import pytest

from token_price_agg.security.anon_limiter import AnonymousRateLimiter


def test_anonymous_rate_limiter_enforces_one_request_per_interval() -> None:
    limiter = AnonymousRateLimiter()

    first = limiter.consume(
        client_id="127.0.0.1",
        min_interval_seconds=5,
        now_ts=1_700_000_000,
    )
    second = limiter.consume(
        client_id="127.0.0.1",
        min_interval_seconds=5,
        now_ts=1_700_000_001,
    )
    third = limiter.consume(
        client_id="127.0.0.1",
        min_interval_seconds=5,
        now_ts=1_700_000_004,
    )
    fourth = limiter.consume(
        client_id="127.0.0.1",
        min_interval_seconds=5,
        now_ts=1_700_000_005,
    )

    assert first.allowed is True
    assert first.request_count == 1
    assert second.allowed is False
    assert second.request_count == 2
    assert third.allowed is False
    assert third.request_count == 3
    assert fourth.allowed is True
    assert fourth.request_count == 1


def test_anonymous_rate_limiter_isolation_by_client_id() -> None:
    limiter = AnonymousRateLimiter()

    a = limiter.consume(client_id="1.1.1.1", min_interval_seconds=5, now_ts=1_700_000_100)
    b = limiter.consume(client_id="2.2.2.2", min_interval_seconds=5, now_ts=1_700_000_100)

    assert a.allowed is True
    assert b.allowed is True


def test_anonymous_rate_limiter_validates_interval() -> None:
    limiter = AnonymousRateLimiter()
    with pytest.raises(ValueError, match="min_interval_seconds must be > 0"):
        limiter.consume(client_id="1.1.1.1", min_interval_seconds=0, now_ts=1)

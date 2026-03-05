from __future__ import annotations

from token_price_agg.security.anon_limiter import AnonymousRateLimiter


def test_anonymous_rate_limiter_enforces_one_request_per_second() -> None:
    limiter = AnonymousRateLimiter()

    first = limiter.consume(client_id="127.0.0.1", limit_rps=1, now_ts=1_700_000_000)
    second = limiter.consume(client_id="127.0.0.1", limit_rps=1, now_ts=1_700_000_000)
    third = limiter.consume(client_id="127.0.0.1", limit_rps=1, now_ts=1_700_000_001)

    assert first.allowed is True
    assert first.request_count == 1
    assert second.allowed is False
    assert second.request_count == 2
    assert third.allowed is True
    assert third.request_count == 1


def test_anonymous_rate_limiter_isolation_by_client_id() -> None:
    limiter = AnonymousRateLimiter()

    a = limiter.consume(client_id="1.1.1.1", limit_rps=1, now_ts=1_700_000_100)
    b = limiter.consume(client_id="2.2.2.2", limit_rps=1, now_ts=1_700_000_100)

    assert a.allowed is True
    assert b.allowed is True

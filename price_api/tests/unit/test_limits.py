from __future__ import annotations

import pytest

from price_api.core.limits import CapacityLimiters, WeightedLimiter


@pytest.mark.asyncio
async def test_weighted_limiter_reserves_units_atomically() -> None:
    limiter = WeightedLimiter(capacity=3)

    first = await limiter.try_acquire(units=2, timeout_ms=0)
    assert first is not None
    assert limiter.used == 2

    second = await limiter.try_acquire(units=2, timeout_ms=0)
    assert second is None
    assert limiter.used == 2

    await first.release()
    assert limiter.used == 0


def test_capacity_limiters_reuse_provider_limiters() -> None:
    limiters = CapacityLimiters(
        global_units=10,
        per_provider_units=2,
    )

    first = limiters.provider_limiter("curve")
    second = limiters.provider_limiter("curve")

    assert second is first


@pytest.mark.asyncio
async def test_capacity_limiters_report_provider_used_units() -> None:
    limiters = CapacityLimiters(
        global_units=10,
        per_provider_units=2,
    )

    reservation = await limiters.provider_limiter("curve").try_acquire(
        units=1,
        timeout_ms=0,
    )
    assert reservation is not None
    assert limiters.provider_used_units() == 1

    await reservation.release()
    assert limiters.provider_used_units() == 0

from __future__ import annotations

from typing import Any, cast

import pytest

import token_price_agg.core.limits as limits_module
from token_price_agg.core.limits import CapacityLimiters, WeightedLimiter


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


def test_capacity_limiters_evict_idle_principal_limiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, limits_module).time, "monotonic", lambda: now)
    limiters = CapacityLimiters(
        global_units=10,
        per_principal_units=2,
        per_provider_units=2,
        principal_idle_ttl_s=10,
        max_principal_limiters=10,
    )

    limiters.principal_limiter("api_key:a")
    assert limiters.principal_limiter_count() == 1

    now = 1011.0
    limiters.principal_limiter("api_key:b")

    assert limiters.principal_limiter_count() == 1


@pytest.mark.asyncio
async def test_capacity_limiters_keep_active_principal_limiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, limits_module).time, "monotonic", lambda: now)
    limiters = CapacityLimiters(
        global_units=10,
        per_principal_units=2,
        per_provider_units=2,
        principal_idle_ttl_s=10,
        max_principal_limiters=10,
    )

    reservation = await limiters.principal_limiter("api_key:a").try_acquire(
        units=1,
        timeout_ms=0,
    )
    assert reservation is not None

    now = 1011.0
    limiters.principal_limiter("api_key:b")
    assert limiters.principal_limiter_count() == 2

    await reservation.release()
    now = 1022.0
    limiters.principal_limiter("api_key:c")
    assert limiters.principal_limiter_count() == 1


def test_capacity_limiters_reuse_oldest_idle_entry_at_principal_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, limits_module).time, "monotonic", lambda: now)
    limiters = CapacityLimiters(
        global_units=10,
        per_principal_units=2,
        per_provider_units=2,
        principal_idle_ttl_s=300,
        max_principal_limiters=1,
    )

    first = limiters.principal_limiter("api_key:a")
    now = 1001.0
    second = limiters.principal_limiter("api_key:b")

    assert second is not first
    assert limiters.principal_limiter_count() == 1


@pytest.mark.asyncio
async def test_capacity_limiters_use_overflow_limiter_when_principal_cap_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, limits_module).time, "monotonic", lambda: now)
    limiters = CapacityLimiters(
        global_units=10,
        per_principal_units=1,
        per_provider_units=2,
        principal_idle_ttl_s=300,
        max_principal_limiters=1,
    )

    active = limiters.principal_limiter("api_key:a")
    reservation = await active.try_acquire(units=1, timeout_ms=0)
    assert reservation is not None

    overflow_one = limiters.principal_limiter("api_key:b")
    overflow_two = limiters.principal_limiter("api_key:c")

    assert overflow_two is overflow_one
    assert overflow_one is not active
    assert limiters.principal_limiter_count() == 1

    await reservation.release()

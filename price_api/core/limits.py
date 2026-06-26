from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


class WeightedLimiter:
    def __init__(self, *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._capacity = capacity
        self._used = 0
        self._condition = asyncio.Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def used(self) -> int:
        return self._used

    async def try_acquire(self, *, units: int, timeout_ms: int) -> LimitReservation | None:
        if units <= 0:
            return LimitReservation(limiter=self, units=0)
        if units > self._capacity:
            return None

        timeout_s = max(timeout_ms, 0) / 1000
        deadline = time.monotonic() + timeout_s
        async with self._condition:
            while self._used + units > self._capacity:
                if timeout_ms <= 0:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    return None

            self._used += units
            return LimitReservation(limiter=self, units=units)

    async def _release(self, units: int) -> None:
        if units <= 0:
            return
        async with self._condition:
            self._used = max(0, self._used - units)
            self._condition.notify_all()


@dataclass(slots=True)
class LimitReservation:
    limiter: WeightedLimiter
    units: int
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self.limiter._release(self.units)


class CapacityLimiters:
    def __init__(
        self,
        *,
        global_units: int,
        per_provider_units: int,
    ) -> None:
        self.global_limiter = WeightedLimiter(capacity=global_units)
        self._per_provider_units = per_provider_units
        self._provider_limiters: dict[str, WeightedLimiter] = {}

    def provider_limiter(self, provider_id: str) -> WeightedLimiter:
        key = provider_id or "unknown"
        limiter = self._provider_limiters.get(key)
        if limiter is None:
            limiter = WeightedLimiter(capacity=self._per_provider_units)
            self._provider_limiters[key] = limiter
        return limiter

    def provider_used_units(self) -> int:
        return sum(limiter.used for limiter in self._provider_limiters.values())

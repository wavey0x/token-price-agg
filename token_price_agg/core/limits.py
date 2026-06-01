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


@dataclass(slots=True)
class _PrincipalLimiterEntry:
    limiter: WeightedLimiter
    last_seen: float


class CapacityLimiters:
    def __init__(
        self,
        *,
        global_units: int,
        per_principal_units: int,
        per_provider_units: int,
        principal_idle_ttl_s: int = 300,
        max_principal_limiters: int = 10000,
    ) -> None:
        self.global_limiter = WeightedLimiter(capacity=global_units)
        self._per_principal_units = per_principal_units
        self._per_provider_units = per_provider_units
        self._principal_idle_ttl_s = principal_idle_ttl_s
        self._max_principal_limiters = max(1, max_principal_limiters)
        self._principal_limiters: dict[str, _PrincipalLimiterEntry] = {}
        self._overflow_principal_limiter = WeightedLimiter(capacity=per_principal_units)
        self._provider_limiters: dict[str, WeightedLimiter] = {}

    def principal_limiter(self, principal_id: str) -> WeightedLimiter:
        now = time.monotonic()
        self._cleanup_principal_limiters(now=now)
        key = principal_id or "unknown"
        entry = self._principal_limiters.get(key)
        if entry is None:
            if len(self._principal_limiters) >= self._max_principal_limiters:
                self._evict_oldest_idle_principal_limiter()
            if len(self._principal_limiters) >= self._max_principal_limiters:
                return self._overflow_principal_limiter
            limiter = WeightedLimiter(capacity=self._per_principal_units)
            self._principal_limiters[key] = _PrincipalLimiterEntry(
                limiter=limiter,
                last_seen=now,
            )
            return limiter

        entry.last_seen = now
        return entry.limiter

    def provider_limiter(self, provider_id: str) -> WeightedLimiter:
        key = provider_id or "unknown"
        limiter = self._provider_limiters.get(key)
        if limiter is None:
            limiter = WeightedLimiter(capacity=self._per_provider_units)
            self._provider_limiters[key] = limiter
        return limiter

    def principal_used_units(self) -> int:
        return (
            sum(entry.limiter.used for entry in self._principal_limiters.values())
            + self._overflow_principal_limiter.used
        )

    def provider_used_units(self) -> int:
        return sum(limiter.used for limiter in self._provider_limiters.values())

    def principal_limiter_count(self) -> int:
        self._cleanup_principal_limiters(now=time.monotonic())
        return len(self._principal_limiters)

    def _cleanup_principal_limiters(self, *, now: float) -> None:
        cutoff = now - self._principal_idle_ttl_s
        stale_keys = [
            key
            for key, entry in self._principal_limiters.items()
            if entry.limiter.used == 0 and entry.last_seen < cutoff
        ]
        for key in stale_keys:
            del self._principal_limiters[key]

    def _evict_oldest_idle_principal_limiter(self) -> None:
        oldest_key: str | None = None
        oldest_seen = float("inf")
        for key, entry in self._principal_limiters.items():
            if entry.limiter.used > 0 or entry.last_seen >= oldest_seen:
                continue
            oldest_key = key
            oldest_seen = entry.last_seen

        if oldest_key is not None:
            del self._principal_limiters[oldest_key]

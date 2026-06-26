from __future__ import annotations

import time
from collections import deque

from price_api.core.errors import ErrorCode, ProviderStatus
from price_api.core.models import PriceResult, QuoteResult
from price_api.observability.metrics import (
    record_provider_circuit_transition,
    set_provider_circuit_state,
)

_RETRIABLE_ERROR_CODES = {
    ErrorCode.TIMEOUT.value,
    ErrorCode.RATE_LIMITED.value,
    ErrorCode.UPSTREAM_HTTP.value,
    ErrorCode.INTERNAL_TRANSPORT_TIMEOUT.value,
}


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_window_s: int,
        failure_threshold: int,
        open_duration_s: int,
        half_open_probe_count: int,
    ) -> None:
        self._failure_window_s = failure_window_s
        self._failure_threshold = failure_threshold
        self._open_duration_s = open_duration_s
        self._half_open_probe_count = half_open_probe_count
        self._failures: dict[str, deque[float]] = {}
        self._open_until: dict[str, float] = {}
        self._half_open_inflight: dict[str, int] = {}
        self._state: dict[str, str] = {}

    def allow(self, provider_id: str) -> bool:
        now = time.monotonic()
        open_until = self._open_until.get(provider_id)
        if open_until is None:
            self._set_state(provider_id, "closed")
            return True

        if now < open_until:
            self._set_state(provider_id, "open")
            return False

        self._set_state(provider_id, "half_open")
        inflight = self._half_open_inflight.get(provider_id, 0)
        if inflight >= self._half_open_probe_count:
            return False
        self._half_open_inflight[provider_id] = inflight + 1
        return True

    def record_result(self, result: PriceResult | QuoteResult) -> None:
        provider_id = result.provider
        is_failure = _is_circuit_failure(result)

        if self._state.get(provider_id) == "half_open":
            self.release_probe(provider_id)
            if is_failure:
                self._open(provider_id, now=time.monotonic())
            elif result.status in {
                ProviderStatus.OK,
                ProviderStatus.NO_ROUTE,
                ProviderStatus.BAD_REQUEST,
            }:
                self._close(provider_id)
            return

        if result.status == ProviderStatus.OK:
            self._close(provider_id)
            return

        if not is_failure:
            return

        now = time.monotonic()
        failures = self._failures.setdefault(provider_id, deque())
        failures.append(now)
        cutoff = now - self._failure_window_s
        while failures and failures[0] < cutoff:
            failures.popleft()

        if len(failures) >= self._failure_threshold:
            self._open(provider_id, now=now)

    def release_probe(self, provider_id: str) -> None:
        if self._state.get(provider_id) != "half_open":
            return
        self._half_open_inflight[provider_id] = max(
            self._half_open_inflight.get(provider_id, 1) - 1,
            0,
        )

    def circuit_open_providers(self) -> set[str]:
        now = time.monotonic()
        return {
            provider_id for provider_id, open_until in self._open_until.items() if now < open_until
        }

    def _close(self, provider_id: str) -> None:
        self._failures.pop(provider_id, None)
        self._open_until.pop(provider_id, None)
        self._half_open_inflight.pop(provider_id, None)
        self._set_state(provider_id, "closed")

    def _open(self, provider_id: str, *, now: float) -> None:
        self._failures.pop(provider_id, None)
        self._half_open_inflight.pop(provider_id, None)
        self._open_until[provider_id] = now + self._open_duration_s
        self._set_state(provider_id, "open")

    def _set_state(self, provider_id: str, state: str) -> None:
        previous = self._state.get(provider_id)
        if previous == state:
            return
        self._state[provider_id] = state
        set_provider_circuit_state(provider=provider_id, state=state)
        record_provider_circuit_transition(provider=provider_id, state=state)


def _is_circuit_failure(result: PriceResult | QuoteResult) -> bool:
    if result.error is None:
        return False
    return result.error.code in _RETRIABLE_ERROR_CODES

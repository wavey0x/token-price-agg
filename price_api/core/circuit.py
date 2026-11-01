from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from price_api.core.errors import ErrorType, ProviderStatus
from price_api.core.models import PriceResult, QuoteResult
from price_api.observability.metrics import (
    record_provider_circuit_transition,
    set_provider_circuit_state,
)

_RETRIABLE_ERROR_TYPES = {
    ErrorType.TIMEOUT.value,
    ErrorType.UPSTREAM_HTTP.value,
    ErrorType.INTERNAL_TRANSPORT_TIMEOUT.value,
}
_MAX_RATE_LIMIT_BACKOFF_S = 300


@dataclass(frozen=True)
class CircuitScope:
    provider: str
    chain_id: int
    operation: str


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
        self._failures: dict[CircuitScope, deque[float]] = {}
        self._open_until: dict[CircuitScope, float] = {}
        self._rate_limit_until: dict[CircuitScope, float] = {}
        self._half_open_inflight: dict[CircuitScope, int] = {}
        self._state: dict[CircuitScope, str] = {}

    def allow(self, scope: CircuitScope) -> bool:
        now = time.monotonic()
        open_until = self._open_until.get(scope)
        if open_until is None:
            self._set_state(scope, "closed")
            return True

        if now < open_until:
            self._set_state(scope, "open")
            return False

        self._set_state(scope, "half_open")
        inflight = self._half_open_inflight.get(scope, 0)
        if inflight >= self._half_open_probe_count:
            return False
        self._half_open_inflight[scope] = inflight + 1
        return True

    def record_result(self, scope: CircuitScope, result: PriceResult | QuoteResult) -> None:
        if result.error is not None and result.error.type == ErrorType.RATE_LIMITED:
            retry_after_s = (result.error.retry_after_ms or self._open_duration_s * 1000) / 1000
            delay_s = min(retry_after_s, max(self._open_duration_s, _MAX_RATE_LIMIT_BACKOFF_S))
            self._open(scope, now=time.monotonic(), delay_s=delay_s)
            self._rate_limit_until[scope] = self._open_until[scope]
            return
        if time.monotonic() < self._rate_limit_until.get(scope, 0):
            return
        is_failure = _is_circuit_failure(result)

        if self._state.get(scope) == "half_open":
            self.release_probe(scope)
            if is_failure:
                self._open(scope, now=time.monotonic())
            elif result.status in {
                ProviderStatus.OK,
                ProviderStatus.NO_ROUTE,
                ProviderStatus.BAD_REQUEST,
            }:
                self._close(scope)
            return

        if result.status == ProviderStatus.OK:
            self._close(scope)
            return

        if not is_failure:
            return

        now = time.monotonic()
        failures = self._failures.setdefault(scope, deque())
        failures.append(now)
        cutoff = now - self._failure_window_s
        while failures and failures[0] < cutoff:
            failures.popleft()

        if len(failures) >= self._failure_threshold:
            self._open(scope, now=now)

    def release_probe(self, scope: CircuitScope) -> None:
        if self._state.get(scope) != "half_open":
            return
        self._half_open_inflight[scope] = max(
            self._half_open_inflight.get(scope, 1) - 1,
            0,
        )

    def circuit_open_providers(self, *, chain_id: int, operation: str) -> set[str]:
        now = time.monotonic()
        return {
            scope.provider
            for scope, open_until in self._open_until.items()
            if now < open_until and scope.chain_id == chain_id and scope.operation == operation
        }

    def _close(self, scope: CircuitScope) -> None:
        self._failures.pop(scope, None)
        self._open_until.pop(scope, None)
        self._rate_limit_until.pop(scope, None)
        self._half_open_inflight.pop(scope, None)
        self._set_state(scope, "closed")

    def _open(self, scope: CircuitScope, *, now: float, delay_s: float | None = None) -> None:
        self._failures.pop(scope, None)
        self._half_open_inflight.pop(scope, None)
        until = now + (self._open_duration_s if delay_s is None else delay_s)
        self._open_until[scope] = max(self._open_until.get(scope, 0), until)
        self._set_state(scope, "open")

    def _set_state(self, scope: CircuitScope, state: str) -> None:
        previous = self._state.get(scope)
        if previous == state:
            return
        self._state[scope] = state
        set_provider_circuit_state(
            provider=scope.provider, chain_id=scope.chain_id, operation=scope.operation, state=state
        )
        record_provider_circuit_transition(
            provider=scope.provider, chain_id=scope.chain_id, operation=scope.operation, state=state
        )


def _is_circuit_failure(result: PriceResult | QuoteResult) -> bool:
    if result.status in {ProviderStatus.BAD_REQUEST, ProviderStatus.NO_ROUTE}:
        return False
    if result.error is None:
        return False
    return result.error.type.value in _RETRIABLE_ERROR_TYPES

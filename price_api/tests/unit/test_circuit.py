from __future__ import annotations

from decimal import Decimal

import pytest

from price_api.core.circuit import CircuitBreaker, CircuitScope
from price_api.core.errors import ErrorInfo, ErrorType, ProviderStatus
from price_api.core.models import PriceResult

SCOPE = CircuitScope("defillama", 1, "price")


def test_half_open_failure_reopens_circuit_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr("price_api.core.circuit.time.monotonic", lambda: now)
    circuit = CircuitBreaker(
        failure_window_s=30,
        failure_threshold=1,
        open_duration_s=10,
        half_open_probe_count=1,
    )

    circuit.record_result(SCOPE, _transport_failure())
    assert circuit.allow(SCOPE) is False

    now = 1011.0
    assert circuit.allow(SCOPE) is True
    circuit.record_result(SCOPE, _transport_failure())

    assert circuit.allow(SCOPE) is False


def test_half_open_probe_can_be_released_without_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr("price_api.core.circuit.time.monotonic", lambda: now)
    circuit = CircuitBreaker(
        failure_window_s=30,
        failure_threshold=1,
        open_duration_s=10,
        half_open_probe_count=1,
    )

    circuit.record_result(SCOPE, _transport_failure())
    now = 1011.0
    assert circuit.allow(SCOPE) is True
    assert circuit.allow(SCOPE) is False

    circuit.release_probe(SCOPE)

    assert circuit.allow(SCOPE) is True


def test_request_specific_failure_does_not_open_shared_circuit() -> None:
    circuit = CircuitBreaker(
        failure_window_s=30,
        failure_threshold=1,
        open_duration_s=10,
        half_open_probe_count=1,
    )
    circuit.record_result(
        SCOPE,
        PriceResult(
            provider="defillama",
            status=ProviderStatus.BAD_REQUEST,
            latency_ms=1,
            error=ErrorInfo(type=ErrorType.UPSTREAM_HTTP, message="bad token"),
        ),
    )

    assert circuit.allow(SCOPE) is True


def _transport_failure() -> PriceResult:
    return PriceResult(
        provider="defillama",
        status=ProviderStatus.ERROR,
        latency_ms=1,
        error=ErrorInfo(type=ErrorType.INTERNAL_TRANSPORT_TIMEOUT, message="pool timeout"),
    )


def test_circuit_failures_and_recovery_are_scoped_to_chain_and_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr("price_api.core.circuit.time.monotonic", lambda: now)
    circuit = CircuitBreaker(
        failure_window_s=30, failure_threshold=1, open_duration_s=10, half_open_probe_count=1
    )
    failed = CircuitScope("curve", 42161, "quote")
    other_chain = CircuitScope("curve", 1, "quote")
    other_operation = CircuitScope("curve", 42161, "price")
    failure = _transport_failure().model_copy(update={"provider": "curve"})
    circuit.record_result(failed, failure)
    assert not circuit.allow(failed)
    assert circuit.allow(other_chain)
    assert circuit.allow(other_operation)

    circuit.record_result(
        other_chain,
        PriceResult(provider="curve", status=ProviderStatus.OK, latency_ms=1, price_usd=Decimal(1)),
    )
    assert not circuit.allow(failed)  # Success elsewhere must not reset this failure.
    assert circuit.circuit_open_providers(chain_id=42161, operation="quote") == {"curve"}
    assert circuit.circuit_open_providers(chain_id=1, operation="quote") == set()
    now += 11
    assert circuit.allow(failed)
    assert not circuit.allow(failed)
    circuit.record_result(
        failed,
        PriceResult(provider="curve", status=ProviderStatus.OK, latency_ms=1, price_usd=Decimal(1)),
    )
    assert circuit.allow(failed)


@pytest.mark.parametrize("retry_after_ms", [None, 30000])
def test_rate_limit_blocks_all_chains_and_operations_until_cooldown_expires(
    monkeypatch: pytest.MonkeyPatch, retry_after_ms: int | None
) -> None:
    now = 1000.0
    monkeypatch.setattr("price_api.core.circuit.time.monotonic", lambda: now)
    circuit = CircuitBreaker(
        failure_window_s=30, failure_threshold=10, open_duration_s=10, half_open_probe_count=1
    )
    circuit.record_result(
        SCOPE,
        PriceResult(
            provider=SCOPE.provider,
            status=ProviderStatus.ERROR,
            latency_ms=1,
            error=ErrorInfo(
                type=ErrorType.RATE_LIMITED, message="quota", retry_after_ms=retry_after_ms
            ),
        ),
    )
    other = CircuitScope(SCOPE.provider, 42161, "quote")
    assert not circuit.allow(SCOPE)
    assert not circuit.allow(other)
    assert circuit.allow(CircuitScope("curve", 42161, "quote"))
    assert circuit.circuit_open_providers(chain_id=42161, operation="quote") == {SCOPE.provider}
    circuit.record_result(
        other,
        PriceResult(
            provider=SCOPE.provider, status=ProviderStatus.OK, latency_ms=1, price_usd=Decimal(1)
        ),
    )
    assert not circuit.allow(SCOPE)  # An already in-flight success must not clear quota backoff.
    now += (retry_after_ms or 10000) / 1000 - 0.1
    assert not circuit.allow(other)
    now += 0.2
    assert circuit.allow(other)
    assert circuit.allow(SCOPE)

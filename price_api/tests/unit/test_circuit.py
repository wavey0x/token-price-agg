from __future__ import annotations

from typing import Any, cast

import pytest

import price_api.core.circuit as circuit_module
from price_api.core.circuit import CircuitBreaker
from price_api.core.errors import ErrorInfo, ErrorType, ProviderStatus
from price_api.core.models import PriceResult


def test_half_open_failure_reopens_circuit_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, circuit_module).time, "monotonic", lambda: now)
    circuit = CircuitBreaker(
        failure_window_s=30,
        failure_threshold=1,
        open_duration_s=10,
        half_open_probe_count=1,
    )

    circuit.record_result(_transport_failure())
    assert circuit.allow("defillama") is False

    now = 1011.0
    assert circuit.allow("defillama") is True
    circuit.record_result(_transport_failure())

    assert circuit.allow("defillama") is False


def test_half_open_probe_can_be_released_without_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, circuit_module).time, "monotonic", lambda: now)
    circuit = CircuitBreaker(
        failure_window_s=30,
        failure_threshold=1,
        open_duration_s=10,
        half_open_probe_count=1,
    )

    circuit.record_result(_transport_failure())
    now = 1011.0
    assert circuit.allow("defillama") is True
    assert circuit.allow("defillama") is False

    circuit.release_probe("defillama")

    assert circuit.allow("defillama") is True


def test_request_specific_failure_does_not_open_shared_circuit() -> None:
    circuit = CircuitBreaker(
        failure_window_s=30,
        failure_threshold=1,
        open_duration_s=10,
        half_open_probe_count=1,
    )
    circuit.record_result(
        PriceResult(
            provider="defillama",
            status=ProviderStatus.BAD_REQUEST,
            latency_ms=1,
            error=ErrorInfo(type=ErrorType.UPSTREAM_HTTP, message="bad token"),
        )
    )

    assert circuit.allow("defillama") is True


def _transport_failure() -> PriceResult:
    return PriceResult(
        provider="defillama",
        status=ProviderStatus.ERROR,
        latency_ms=1,
        error=ErrorInfo(type=ErrorType.INTERNAL_TRANSPORT_TIMEOUT, message="pool timeout"),
    )

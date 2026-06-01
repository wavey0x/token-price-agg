from __future__ import annotations

from typing import Any, cast

import pytest

import token_price_agg.core.circuit as circuit_module
from token_price_agg.core.circuit import CircuitBreaker
from token_price_agg.core.errors import ErrorInfo, ProviderStatus
from token_price_agg.core.models import PriceResult


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


def _transport_failure() -> PriceResult:
    return PriceResult(
        provider="defillama",
        status=ProviderStatus.ERROR,
        latency_ms=1,
        error=ErrorInfo(code="INTERNAL_TRANSPORT_TIMEOUT", message="pool timeout"),
    )

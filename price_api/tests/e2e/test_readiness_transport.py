from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from price_api.app.main import app


def test_metrics_endpoint_normalizes_unknown_paths() -> None:
    with TestClient(app) as client:
        missing = client.get("/unknown-scanner-path")
        assert missing.status_code == 404
        response = client.get("/metrics")

    assert response.status_code == 200
    assert 'endpoint="/unknown",method="GET"' in response.text


def test_readiness_endpoint_returns_503_when_provider_transport_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "price_api.providers.registry.ProviderRegistry.transport_unhealthy",
        lambda _: True,
    )

    with TestClient(app) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"]["reason"] == "provider_transport_unhealthy"


def test_readiness_endpoint_returns_503_when_close_wait_threshold_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recycle_reasons: list[str] = []

    async def _recycle_transports(_: object, *, reason: str) -> None:
        recycle_reasons.append(reason)

    monkeypatch.setenv("CLOSE_WAIT_READY_THRESHOLD", "2")
    monkeypatch.setattr(
        "price_api.api.routes.ready.count_process_close_wait_sockets",
        lambda: 3,
    )
    monkeypatch.setattr(
        "price_api.providers.registry.ProviderRegistry.recycle_transports",
        _recycle_transports,
    )

    with TestClient(app) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"]["reason"] == "close_wait_threshold_exceeded"
    assert payload["checks"]["close_wait_sockets"] == 3
    assert recycle_reasons == ["close_wait"]

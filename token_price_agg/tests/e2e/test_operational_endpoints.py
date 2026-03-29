from __future__ import annotations

from typing import TypeAlias

import pytest
from fastapi.testclient import TestClient

from token_price_agg.app.main import app
from token_price_agg.tests.e2e.helpers import token_lower

QueryParamValue: TypeAlias = str | int
RequestParams: TypeAlias = dict[str, QueryParamValue]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (
            "/v1/price",
            {
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "lifi",
                "timeout_ms": 10000,
            },
        ),
        (
            "/v1/quote",
            {
                "chain_id": 1,
                "token_in": token_lower("USDC"),
                "token_out": token_lower("CRV"),
                "amount_in": "1000000",
                "providers": "lifi",
                "timeout_ms": 10000,
            },
        ),
    ],
)
def test_timeout_ms_accepts_documented_max(path: str, params: RequestParams) -> None:
    with TestClient(app) as client:
        response = client.get(path, params=params)

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (
            "/v1/price",
            {
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "lifi",
                "timeout_ms": 10001,
            },
        ),
        (
            "/v1/quote",
            {
                "chain_id": 1,
                "token_in": token_lower("USDC"),
                "token_out": token_lower("CRV"),
                "amount_in": "1000000",
                "providers": "lifi",
                "timeout_ms": 10001,
            },
        ),
    ],
)
def test_timeout_ms_rejects_above_max(path: str, params: RequestParams) -> None:
    with TestClient(app) as client:
        response = client.get(path, params=params)

    assert response.status_code == 422


def test_providers_endpoint_shows_missing_api_keys() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/providers")

    assert response.status_code == 200
    payload = response.json()
    providers = {item["id"]: item for item in payload["providers"]}

    assert providers["lifi"]["available"] is False
    assert providers["lifi"]["unavailable_reason"] == "missing_api_key"
    assert providers["enso"]["available"] is False
    assert providers["odos"]["available"] is True


def test_removed_plural_and_post_paths_return_404() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/prices").status_code == 404
        assert client.get("/v1/quotes").status_code == 404
        assert client.post("/v1/prices", json={}).status_code == 404
        assert client.post("/v1/quotes", json={}).status_code == 404


def test_request_id_header_round_trip() -> None:
    request_id = "req-id-from-client"

    with TestClient(app) as client:
        response = client.get("/v1/health", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


def test_metrics_endpoint_exposes_prometheus_payload() -> None:
    with TestClient(app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200

        price = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "lifi",
            },
        )
        assert price.status_code == 200

        response = client.get("/metrics")

    assert response.status_code == 200
    assert "token_price_agg_http_requests_total" in response.text
    assert "token_price_agg_http_request_latency_seconds" in response.text
    assert "token_price_agg_provider_calls_total" in response.text
    assert 'endpoint="/v1/price",method="GET"' in response.text


def test_readiness_endpoint_default_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["provider_registry"] is True


def test_readiness_endpoint_strict_returns_503_without_available_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDERS_ENABLED", "lifi,enso")
    monkeypatch.setenv("LIFI_API_KEY", "")
    monkeypatch.setenv("ENSO_API_KEY", "")
    monkeypatch.setenv("ENABLE_READINESS_STRICT", "true")
    with TestClient(app) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"]["reason"] == "no_available_providers"

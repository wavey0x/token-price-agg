from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from token_price_agg.app.main import app
from token_price_agg.tests.e2e.helpers import issue_test_api_key, token_lower


def test_auth_enabled_requires_bearer_token_for_v1_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    key = issue_test_api_key("auth-e2e")

    with TestClient(app) as client:
        unauthorized = client.get("/v1/health")
        authorized = client.get("/v1/health", headers={"Authorization": f"Bearer {key}"})
        metrics = client.get("/metrics")

    assert unauthorized.status_code == 401
    assert unauthorized.json()["detail"]["code"] == "UNAUTHORIZED"
    assert unauthorized.headers["WWW-Authenticate"] == "Bearer"

    assert authorized.status_code == 200
    assert authorized.json()["status"] == "ok"

    assert metrics.status_code == 200


def test_auth_enabled_protects_price_ready_and_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    key = issue_test_api_key("price-e2e")

    with TestClient(app) as client:
        no_key_price = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "lifi",
            },
        )
        with_key_price = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "lifi",
            },
            headers={"Authorization": f"Bearer {key}"},
        )
        no_key_ready = client.get("/v1/ready")
        no_key_providers = client.get("/v1/providers")

    assert no_key_price.status_code == 401
    assert no_key_ready.status_code == 401
    assert no_key_providers.status_code == 401

    assert with_key_price.status_code == 200
    assert with_key_price.json()["summary"]["requested_providers"] == 1


def test_rate_limit_returns_429_with_headers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY_RATE_LIMIT_RPM", "3")
    key = issue_test_api_key("limit-e2e")

    with TestClient(app) as client:
        first = client.get("/v1/health", headers={"Authorization": f"Bearer {key}"})
        second = client.get("/v1/health", headers={"Authorization": f"Bearer {key}"})
        third = client.get("/v1/health", headers={"Authorization": f"Bearer {key}"})
        fourth = client.get("/v1/health", headers={"Authorization": f"Bearer {key}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200

    assert fourth.status_code == 429
    payload = fourth.json()
    assert payload["detail"]["code"] == "RATE_LIMITED"
    assert "Retry-After" in fourth.headers
    assert fourth.headers["X-RateLimit-Limit"] == "3"
    assert fourth.headers["X-RateLimit-Remaining"] == "0"
    assert "X-RateLimit-Reset" in fourth.headers

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from token_price_agg.app.main import app
from token_price_agg.security.store import ApiKeyStore
from token_price_agg.tests.e2e.helpers import issue_test_api_key, token_lower


def test_auth_enabled_allows_missing_authorization_at_limited_rate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY_UNAUTH_ACCESS_ENABLED", "true")
    monkeypatch.setenv("API_KEY_UNAUTH_RATE_LIMIT_RPS", "1")
    key = issue_test_api_key("auth-e2e")

    with TestClient(app) as client:
        first = client.get("/v1/health")

        limited = None
        for _ in range(6):
            response = client.get("/v1/health")
            if response.status_code == 429:
                limited = response
                break

        authorized = client.get("/v1/health", headers={"Authorization": f"Bearer {key}"})
        metrics = client.get("/metrics")

    assert first.status_code == 200
    assert limited is not None
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "RATE_LIMITED"
    assert limited.headers["X-RateLimit-Limit"] == "1"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in limited.headers
    assert "X-RateLimit-Reset" in limited.headers

    assert authorized.status_code == 200
    assert authorized.json()["status"] == "ok"
    assert metrics.status_code == 200


def test_auth_enabled_with_unauth_access_disabled_requires_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY_UNAUTH_ACCESS_ENABLED", "false")
    key = issue_test_api_key("strict-e2e")

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


def test_auth_enabled_invalid_authorization_header_is_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY_UNAUTH_ACCESS_ENABLED", "true")

    with TestClient(app) as client:
        response = client.get("/v1/health", headers={"Authorization": "Token not-bearer"})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


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


def test_per_key_rate_limit_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY_RATE_LIMIT_RPM", "10")

    store = ApiKeyStore(db_path=str(db_path))
    issued = store.issue_key(label="override-e2e")
    assert store.set_key_rate_limit(public_id=issued.public_id, rate_limit_rpm=2) is True

    with TestClient(app) as client:
        first = client.get("/v1/health", headers={"Authorization": f"Bearer {issued.key}"})
        second = client.get("/v1/health", headers={"Authorization": f"Bearer {issued.key}"})
        third = client.get("/v1/health", headers={"Authorization": f"Bearer {issued.key}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers["X-RateLimit-Limit"] == "2"

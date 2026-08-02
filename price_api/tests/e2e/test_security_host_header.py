from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from price_api.app.main import app


def test_auth_cannot_be_bypassed_with_path_in_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api_keys.sqlite3"
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY_UNAUTH_ACCESS_ENABLED", "false")

    with TestClient(app) as client:
        control = client.get("/v1/providers")
        malformed_host = client.get(
            "/v1/providers",
            headers={"Host": "example.test/bypass"},
        )

    assert control.status_code == 401
    assert malformed_host.status_code == 401
    assert malformed_host.json()["detail"]["type"] == "UNAUTHORIZED"

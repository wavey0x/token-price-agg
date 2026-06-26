from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from price_api.app.dependencies import get_token_metadata_resolver
from price_api.app.main import app
from price_api.core.models import TokenMetadata
from price_api.core.validator import AddressValidator
from price_api.tests.e2e.helpers import token_lower
from price_api.token_metadata.resolver import TokenMetadataResolver

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
REMAPPED_REQUEST = "0xa3cc91589feedbbee0cfdc7404041e19cb00f110"
REMAPPED_CANONICAL = "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B"


@pytest.fixture(autouse=True)
def _disable_logo_source_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_refresh(
        self: TokenMetadataResolver,
        *,
        force: bool = False,
    ) -> dict[int, dict[str, int]]:
        del self, force
        return {}

    monkeypatch.setattr(TokenMetadataResolver, "refresh_logo_sources", _no_refresh)


def test_token_endpoint_returns_cached_metadata_for_known_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TOKEN_METADATA_DB_PATH", str(tmp_path / "token_metadata.sqlite3"))
    resolver = get_token_metadata_resolver()
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                symbol="USDC",
                decimals=6,
                logo_url="https://assets.example.com/usdc.png",
                logo_status="valid",
                logo_source="coingecko",
                logo_checked_at=int(time.time()),
                logo_http_status=200,
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/v1/token", params={"token": token_lower("USDC")})

    assert response.status_code == 200
    payload = response.json()
    assert payload["chain_id"] == 1
    assert payload["token"] == {
        "chain_id": 1,
        "address": USDC,
        "symbol": "USDC",
        "decimals": 6,
        "logo_url": "https://assets.example.com/usdc.png",
    }


def test_token_endpoint_uses_original_requested_address_when_remapped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TOKEN_METADATA_DB_PATH", str(tmp_path / "token_metadata.sqlite3"))
    resolver = get_token_metadata_resolver()
    resolver._cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=REMAPPED_CANONICAL,
                symbol="YFI-LP",
                decimals=18,
                logo_url="https://assets.example.com/yfi-lp.png",
                logo_status="valid",
                logo_source="local_override",
                logo_checked_at=int(time.time()),
                logo_http_status=200,
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/v1/token", params={"chain_id": 1, "token": REMAPPED_REQUEST})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"]["address"] == AddressValidator.normalize_address(REMAPPED_REQUEST)
    assert payload["token"]["symbol"] == "YFI-LP"
    assert payload["token"]["decimals"] == 18
    assert payload["token"]["logo_url"] == "https://assets.example.com/yfi-lp.png"


def test_token_endpoint_invalid_address_returns_bad_request() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/token",
            params={"chain_id": 1, "token": "0xgggggggggggggggggggggggggggggggggggggggg"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_ADDRESS"


def test_openapi_includes_token_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/v1/token" in schema["paths"]
    assert (
        schema["paths"]["/v1/token"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/TokenResponse"
    )

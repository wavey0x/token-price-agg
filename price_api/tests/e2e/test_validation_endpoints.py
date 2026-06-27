from __future__ import annotations

from fastapi.testclient import TestClient

from price_api.app.main import app
from price_api.tests.e2e.helpers import token_lower


def test_price_endpoint_invalid_token_returns_bad_request() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": "0x" + ("g" * 40),
                "providers": "defillama",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "INVALID_ADDRESS"


def test_quote_endpoint_invalid_amount_returns_bad_request() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/v1/quote",
            params={
                "chain_id": 1,
                "token_in": token_lower("CRV"),
                "token_out": token_lower("USDC"),
                "amount_in": "0",
                "providers": "curve",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "INVALID_AMOUNT"


def test_quote_endpoint_oversized_amount_returns_bad_request() -> None:
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/v1/quote",
            params={
                "chain_id": 1,
                "token_in": token_lower("CRV"),
                "token_out": token_lower("USDC"),
                "amount_in": "1" * 79,
                "providers": "curve",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "INVALID_AMOUNT"

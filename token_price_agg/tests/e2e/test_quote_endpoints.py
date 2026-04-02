from __future__ import annotations

import asyncio
import time

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from token_price_agg.app.dependencies import get_provider_registry
from token_price_agg.app.main import app
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import QuoteResult, TokenRef
from token_price_agg.tests.e2e.helpers import QUOTE_PAIRS, token, token_lower


@pytest.mark.parametrize("token_in_symbol,token_out_symbol", QUOTE_PAIRS)
def test_quote_endpoint_supports_default_quote_matrix(
    token_in_symbol: str,
    token_out_symbol: str,
) -> None:
    token_in_checksum = token(token_in_symbol)
    token_out_checksum = token(token_out_symbol)

    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "amountOut": "1000000",
                        "amountOutMin": "990000",
                        "estimatedGas": 210000,
                        "priceImpact": "0.002",
                        "route": {"hops": 2},
                    }
                },
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params={
                    "chain_id": 1,
                    "token_in": token_lower(token_in_symbol),
                    "token_out": token_lower(token_out_symbol),
                    "amount_in": "1000000000000000000",
                    "providers": "curve",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_providers"] == 1
    assert payload["token_in"]["address"] == token_in_checksum
    assert payload["token_out"]["address"] == token_out_checksum
    assert payload["provider_order"] == ["curve"]
    assert payload["quote"]["provider"] == "curve"
    assert payload["providers"]["curve"]["status"] == "ok"
    assert payload["providers"]["curve"]["route"] is None
    assert "vault_context" not in payload["providers"]["curve"]
    assert payload["summary"]["high_amount_out"] == 1000000
    assert payload["summary"]["low_amount_out"] == 1000000
    assert payload["summary"]["median_amount_out"] == 1000000
    assert "best_amount_out" not in payload["summary"]
    assert "best_provider" not in payload["summary"]
    assert "partial" not in payload
    assert "query_type" not in payload

    assert "tokens" not in payload
    assert "results" not in payload
    assert "request_token_in" not in payload
    assert "request_token_out" not in payload


@pytest.mark.parametrize(
    "provider_params",
    [
        [("providers", "curve"), ("providers", "defillama")],
        [("providers", "curve,defillama")],
        [("providers", "curve,defillama"), ("providers", "curve")],
    ],
)
def test_quote_endpoint_provider_query_styles(provider_params: list[tuple[str, str]]) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "amountOut": "1000000",
                        "amountOutMin": "990000",
                        "estimatedGas": 210000,
                        "priceImpact": "0.002",
                        "route": {"hops": 2},
                    }
                },
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params=[
                    ("chain_id", "1"),
                    ("token_in", token_lower("CRV")),
                    ("token_out", token_lower("USDC")),
                    ("amount_in", "1000000000000000000"),
                    *provider_params,
                ],
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_providers"] == 2
    assert payload["summary"]["successful_providers"] == 1
    assert payload["summary"]["failed_providers"] == 1
    assert payload["provider_order"] == ["curve", "defillama"]

    assert payload["providers"]["curve"]["status"] == "ok"
    assert payload["providers"]["defillama"]["status"] == "bad_request"
    assert payload["quote"]["provider"] == "curve"


def test_quote_endpoint_include_route_true_preserves_route() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "amountOut": "1000000",
                        "amountOutMin": "990000",
                        "estimatedGas": 210000,
                        "priceImpact": "0.002",
                        "route": {"hops": 2},
                    }
                },
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params={
                    "chain_id": 1,
                    "token_in": token_lower("CRV"),
                    "token_out": token_lower("USDC"),
                    "amount_in": "1000000000000000000",
                    "providers": "curve",
                    "include_route": "true",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"]["curve"]["route"] == {"hops": 2}
    assert payload["quote"]["route"] == {"hops": 2}


def test_quote_endpoint_defaults_chain_id_to_mainnet_when_missing() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "amountOut": "1000000",
                        "amountOutMin": "990000",
                        "estimatedGas": 210000,
                        "priceImpact": "0.002",
                        "route": {"hops": 2},
                    }
                },
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params={
                    "token_in": token_lower("CRV"),
                    "token_out": token_lower("USDC"),
                    "amount_in": "1000000000000000000",
                    "providers": "curve",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chain_id"] == 1
    assert payload["providers"]["curve"]["status"] == "ok"


def test_quote_endpoint_use_underlying_is_best_effort_without_rpc() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(
                200,
                json={
                    "data": {
                        "amountOut": "1000000",
                        "amountOutMin": "990000",
                        "estimatedGas": 210000,
                        "priceImpact": "0.002",
                    }
                },
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params={
                    "chain_id": 1,
                    "token_in": token_lower("CRV"),
                    "token_out": token_lower("USDC"),
                    "amount_in": "1000000000000000000",
                    "providers": "curve",
                    "use_underlying": "true",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"]["curve"]["status"] == "ok"


def test_quote_endpoint_curve_empty_list_maps_to_no_route() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(200, json=[]),
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params={
                    "chain_id": 1,
                    "token_in": token_lower("CRV"),
                    "token_out": "0xb5571e76693ba60110b5811dd650ffefce1c955f",
                    "amount_in": "3046763837527638654979",
                    "providers": "curve",
                    "use_underlying": "true",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["quote"] is None
    assert payload["providers"]["curve"]["status"] == "no_route"
    assert payload["providers"]["curve"]["success"] is False
    assert payload["providers"]["curve"]["error"]["code"] == "NO_ROUTE"
    assert payload["providers"]["curve"]["error"]["message"] == "No route found"


def test_openapi_quote_vault_context_uses_leg_specific_price_per_share_fields() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()["components"]["schemas"]["QuoteVaultContext"]["properties"]
    assert "price_per_share_token_in" in schema
    assert "price_per_share_token_out" in schema
    assert "price_per_share" not in schema


def test_quote_endpoint_plugin_exception_returns_internal_error_not_http_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = get_provider_registry()
    curve = registry._plugins["curve"]

    async def _boom(*_: object, **__: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(curve, "get_quote", _boom)

    with TestClient(app) as client:
        response = client.get(
            "/v1/quote",
            params={
                "chain_id": 1,
                "token_in": token_lower("CRV"),
                "token_out": token_lower("USDC"),
                "amount_in": "1000000000000000000",
                "providers": "curve",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_providers"] == 1
    assert payload["summary"]["failed_providers"] == 1
    assert payload["provider_order"] == ["curve"]
    assert payload["quote"] is None
    assert payload["providers"]["curve"]["status"] == "error"
    assert payload["providers"]["curve"]["success"] is False


def test_quote_endpoint_deadline_exceeded_returns_timeout_and_fast_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_REQUEST_TIMEOUT_MS", "50")
    registry = get_provider_registry()
    curve = registry._plugins["curve"]
    token_in = token("CRV")
    token_out = token("USDC")

    async def _slow(*_: object, **__: object) -> QuoteResult:
        await asyncio.sleep(1.0)
        return QuoteResult(
            provider="curve",
            status=ProviderStatus.OK,
            token_in=TokenRef(chain_id=1, address=token_in),
            token_out=TokenRef(chain_id=1, address=token_out),
            amount_in=10**18,
            amount_out=1,
            latency_ms=1000,
        )

    monkeypatch.setattr(curve, "get_quote", _slow)

    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.get(
            "/v1/quote",
            params={
                "chain_id": 1,
                "token_in": token_lower("CRV"),
                "token_out": token_lower("USDC"),
                "amount_in": "1000000000000000000",
                "providers": "curve",
            },
        )
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 0.70
    payload = response.json()
    assert payload["quote"] is None
    assert payload["providers"]["curve"]["status"] == "error"
    assert payload["providers"]["curve"]["success"] is False
    assert payload["providers"]["curve"]["error"]["code"] == "DEADLINE_EXCEEDED"

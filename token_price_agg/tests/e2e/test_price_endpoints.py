from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from token_price_agg.app.dependencies import get_provider_registry
from token_price_agg.app.main import app
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import PriceResult, TokenRef
from token_price_agg.tests.e2e.helpers import (
    mock_defillama_price,
    token,
    token_lower,
)
from token_price_agg.tests.fixtures.ethereum_tokens import DEFAULT_PRICE_SYMBOLS


@pytest.mark.parametrize("symbol", DEFAULT_PRICE_SYMBOLS)
def test_price_endpoint_returns_new_shape_for_default_symbols(symbol: str) -> None:
    token_checksum = token(symbol)

    with respx.mock(assert_all_called=True) as router:
        mock_defillama_price(router, token_checksum, symbol)

        with TestClient(app) as client:
            response = client.get(
                "/v1/price",
                params={
                    "chain_id": 1,
                    "token": token_lower(symbol),
                    "providers": "defillama",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["successful_providers"] == 1
    assert "partial" not in payload
    assert "query_type" not in payload

    assert payload["token"]["address"] == token_checksum
    assert payload["provider_order"] == ["defillama"]
    assert payload["price_data"]["provider"] == "defillama"
    assert payload["providers"]["defillama"]["status"] == "ok"
    assert "best_price" in payload["summary"]
    assert "high_price" in payload["summary"]
    assert "low_price" in payload["summary"]
    assert "median_price" in payload["summary"]
    assert Decimal(str(payload["summary"]["high_price"])) == Decimal("1")
    assert Decimal(str(payload["summary"]["low_price"])) == Decimal("1")

    assert "value_usd" not in payload["price_data"]
    assert "value_usd" not in payload["providers"]["defillama"]
    assert "price_usd" not in payload["price_data"]
    assert "price_usd" not in payload["providers"]["defillama"]
    assert "best_price_usd" not in payload["summary"]
    assert "median_price_usd" not in payload["summary"]
    assert "tokens" not in payload
    assert "results" not in payload
    assert "request_token" not in payload


@pytest.mark.parametrize(
    "provider_params",
    [
        [("providers", "curve"), ("providers", "defillama")],
        [("providers", "curve,defillama")],
        [("providers", "curve,defillama"), ("providers", "curve")],
    ],
)
def test_price_endpoint_provider_query_styles(provider_params: list[tuple[str, str]]) -> None:
    token_checksum = token("USDC")

    with respx.mock(assert_all_called=True) as router:
        mock_defillama_price(router, token_checksum, "USDC")
        router.get(f"https://prices.curve.finance/v1/usd_price/ethereum/{token_checksum}").mock(
            return_value=Response(
                200,
                json={"data": {"usd_price": "1.01", "timestamp": 1700000010}},
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/price",
                params=[("chain_id", "1"), ("token", token_lower("USDC")), *provider_params],
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_providers"] == 2
    assert payload["summary"]["successful_providers"] == 2
    assert Decimal(str(payload["summary"]["best_price"])) == Decimal("1.01")
    assert Decimal(str(payload["summary"]["high_price"])) == Decimal("1.01")
    assert Decimal(str(payload["summary"]["low_price"])) == Decimal("1.00")
    assert payload["provider_order"] == ["curve", "defillama"]
    assert sorted(payload["providers"].keys()) == ["curve", "defillama"]
    assert payload["price_data"]["provider"] == "curve"


def test_price_endpoint_default_precedence_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_PROVIDER_PRIORITY", "defillama,curve")
    token_checksum = token("USDC")

    with respx.mock(assert_all_called=True) as router:
        mock_defillama_price(router, token_checksum, "USDC")
        router.get(f"https://prices.curve.finance/v1/usd_price/ethereum/{token_checksum}").mock(
            return_value=Response(
                200,
                json={"data": {"usd_price": "1.01", "timestamp": 1700000010}},
            )
        )

        with TestClient(app) as client:
            response = client.get(
                "/v1/price",
                params={
                    "chain_id": 1,
                    "token": token_lower("USDC"),
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_order"] == ["defillama", "curve"]
    assert payload["price_data"]["provider"] == "defillama"


def test_price_endpoint_defaults_chain_id_to_mainnet_when_missing() -> None:
    token_checksum = token("USDC")

    with respx.mock(assert_all_called=True) as router:
        mock_defillama_price(router, token_checksum, "USDC")

        with TestClient(app) as client:
            response = client.get(
                "/v1/price",
                params={
                    "token": token_lower("USDC"),
                    "providers": "defillama",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chain_id"] == 1
    assert payload["providers"]["defillama"]["status"] == "ok"


def test_price_endpoint_explicit_unavailable_provider_returns_result_not_http_error() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "lifi",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_order"] == ["lifi"]
    assert payload["price_data"] is None
    assert payload["providers"]["lifi"]["status"] == "invalid_request"
    assert payload["providers"]["lifi"]["success"] is False


def test_price_endpoint_plugin_exception_returns_internal_error_not_http_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = get_provider_registry()
    curve = registry._plugins["curve"]

    async def _boom(*_: object, **__: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(curve, "get_price", _boom)

    with TestClient(app) as client:
        response = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "curve",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_providers"] == 1
    assert payload["summary"]["failed_providers"] == 1
    assert payload["provider_order"] == ["curve"]
    assert payload["price_data"] is None
    assert payload["providers"]["curve"]["status"] == "internal_error"
    assert payload["providers"]["curve"]["success"] is False


def test_price_endpoint_deadline_exceeded_returns_timeout_and_fast_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_REQUEST_TIMEOUT_MS", "50")
    registry = get_provider_registry()
    curve = registry._plugins["curve"]
    token_address = token("USDC")

    async def _slow(*_: object, **__: object) -> PriceResult:
        await asyncio.sleep(1.0)
        return PriceResult(
            provider="curve",
            status=ProviderStatus.OK,
            token=TokenRef(chain_id=1, address=token_address),
            price_usd=Decimal("1"),
            latency_ms=1000,
        )

    monkeypatch.setattr(curve, "get_price", _slow)

    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.get(
            "/v1/price",
            params={
                "chain_id": 1,
                "token": token_lower("USDC"),
                "providers": "curve",
            },
        )
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 0.50
    payload = response.json()
    assert payload["price_data"] is None
    assert payload["providers"]["curve"]["status"] == "timeout"
    assert payload["providers"]["curve"]["success"] is False
    assert payload["providers"]["curve"]["error"]["code"] == "DEADLINE_EXCEEDED"


def test_price_endpoint_one_provider_fails_other_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = get_provider_registry()
    curve = registry._plugins["curve"]

    async def _boom(*_: object, **__: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(curve, "get_price", _boom)

    token_checksum = token("USDC")

    with respx.mock(assert_all_called=True) as router:
        mock_defillama_price(router, token_checksum, "USDC")

        with TestClient(app) as client:
            response = client.get(
                "/v1/price",
                params={
                    "chain_id": 1,
                    "token": token_lower("USDC"),
                    "providers": "curve,defillama",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["requested_providers"] == 2
    assert payload["summary"]["successful_providers"] == 1
    assert payload["summary"]["failed_providers"] == 1
    assert payload["provider_order"] == ["curve", "defillama"]

    assert payload["providers"]["curve"]["status"] == "internal_error"
    assert payload["providers"]["defillama"]["status"] == "ok"
    assert payload["price_data"]["provider"] == "defillama"

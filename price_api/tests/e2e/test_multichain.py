from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from price_api.app.main import app
from price_api.chains import CHAINS
from price_api.tests.e2e.helpers import token


@pytest.mark.parametrize("chain_id", CHAINS)
def test_prices_use_the_requested_network(chain_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_IDS", str(chain_id))
    # Deliberately reuse the same address: network, not address, must select the price.
    address = token("USDC")
    chain = CHAINS[chain_id]
    coin = f"{chain.defillama_name}:{address}"
    with respx.mock(assert_all_called=True) as router:
        router.get(f"https://coins.llama.fi/prices/current/{coin}").respond(
            200, json={"coins": {coin: {"price": "1", "symbol": "USDC", "decimals": 6}}}
        )
        router.get(
            f"https://prices.curve.finance/v1/usd_price/{chain.curve_name}/{address}"
        ).respond(200, json={"price": "1.01"})
        with TestClient(app) as client:
            assert client.get("/v1/health").status_code == 200
            payload = client.get(
                "/v1/price",
                params={"chain_id": chain_id, "token": address, "providers": "curve,defillama"},
            ).json()
            capabilities = client.get("/v1/providers").json()
    assert payload["chain_id"] == chain_id
    assert payload["summary"]["successful_providers"] == 2
    assert payload["token"]["chain_id"] == chain_id
    assert all(p["supported_chains"] == [chain_id] for p in capabilities["providers"])


@pytest.mark.parametrize("chain_id", CHAINS)
def test_quote_fallback_keeps_both_legs_on_requested_chain(
    chain_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHAIN_IDS", str(chain_id))
    monkeypatch.setenv("LIFI_API_KEY", "test-key")
    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").respond(503)
        lifi = router.get("https://li.quest/v1/quote").mock(
            return_value=Response(
                200,
                json={
                    "action": {"fromToken": {"decimals": 6}, "toToken": {"decimals": 18}},
                    "estimate": {"toAmount": "12300000000000000"},
                },
            )
        )
        with TestClient(app) as client:
            response = client.get(
                "/v1/quote",
                params={
                    "chain_id": chain_id,
                    "token_in": token("USDC"),
                    "token_out": token("CRV"),
                    "amount_in": "100000000",
                    "providers": "curve,lifi",
                },
            )
    assert response.status_code == 200
    payload = response.json()
    assert payload["quote"]["provider"] == "lifi"
    assert payload["quote"]["amount_out"] == "12300000000000000"
    assert payload["summary"]["successful_providers"] == 1
    assert payload["summary"]["failed_providers"] == 1
    assert payload["token_in"]["chain_id"] == payload["token_out"]["chain_id"] == chain_id
    params = lifi.calls.last.request.url.params
    assert params["fromChain"] == params["toChain"] == str(chain_id)
    assert params["fromAmount"] == "100000000"


@pytest.mark.parametrize("path", ["/v1/token", "/v1/price", "/v1/quote"])
def test_disabled_chain_is_rejected_consistently(path: str) -> None:
    with TestClient(app) as client:
        response = client.get(
            path,
            params={
                "chain_id": 42161,
                "token": token("USDC"),
                "token_in": token("USDC"),
                "token_out": token("CRV"),
                "amount_in": "1000000",
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "UNSUPPORTED_CHAIN"


def test_underlying_request_requires_its_own_chain_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_IDS", "1,42161")
    monkeypatch.setenv("ETHEREUM_RPC_URL", "https://ethereum.example")
    # No upstream/RPC mock: any attempted request (including Ethereum fallback) fails the test.
    with respx.mock(assert_all_called=True), TestClient(app) as client:
        response = client.get(
            "/v1/price",
            params={
                "chain_id": 42161,
                "token": token("USDC"),
                "providers": "curve",
                "use_underlying": "true",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["price_data"] is None
    assert payload["providers"]["curve"]["error"]["type"] == "VAULT_RESOLUTION_FAILED"

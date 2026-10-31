from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from price_api.app.dependencies import get_provider_registry, get_vault_resolver
from price_api.app.main import app
from price_api.chains import CHAINS
from price_api.tests.e2e.helpers import token
from price_api.vault.adapters.erc4626 import Erc4626VaultInfo


@pytest.mark.parametrize(
    ("chain_id", "defillama_name", "curve_name"),
    [
        (1, "ethereum", "ethereum"),
        (10, "optimism", "optimism"),
        (100, "xdai", "xdai"),
        (137, "polygon", "polygon"),
        (42161, "arbitrum", "arbitrum"),
        (43114, "avax", "avalanche"),
    ],
)
def test_prices_use_the_requested_network(
    chain_id: int, defillama_name: str, curve_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHAIN_IDS", str(chain_id))
    # Deliberately reuse the same address: network, not address, must select the price.
    address = token("USDC")
    coin = f"{defillama_name}:{address}"
    with respx.mock(assert_all_called=True) as router:
        router.get(f"https://coins.llama.fi/prices/current/{coin}").respond(
            200, json={"coins": {coin: {"price": "1", "symbol": "USDC", "decimals": 6}}}
        )
        router.get(f"https://prices.curve.finance/v1/usd_price/{curve_name}/{address}").respond(
            200, json={"price": "1.01"}
        )
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
        curve = router.get("https://www.curve.finance/api/router/v1/routes").respond(503)
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
    assert curve.calls.last.request.url.params["chainId"] == str(chain_id)


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


def test_chain_and_operation_circuits_are_isolated_in_one_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAIN_IDS", "1,42161")
    monkeypatch.setenv("PROVIDERS_ENABLED", "curve")
    monkeypatch.setenv("PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "2")
    with respx.mock(assert_all_called=True) as router:
        arbitrum = router.get(
            f"https://prices.curve.finance/v1/usd_price/arbitrum/{token('USDC')}"
        ).respond(503)
        ethereum = router.get(
            f"https://prices.curve.finance/v1/usd_price/ethereum/{token('USDC')}"
        ).respond(200, json={"price": "1"})
        router.get("https://www.curve.finance/api/router/v1/routes").respond(
            200, json={"amountOut": "1000000"}
        )
        with TestClient(app) as client:
            assert client.get("/v1/health").status_code == 200
            params = {"chain_id": 42161, "token": token("USDC"), "providers": "curve"}
            for _ in range(3):
                failed = client.get("/v1/price", params=params)
                assert failed.status_code == 200
                assert failed.json()["price_data"] is None
            assert arbitrum.call_count == 2
            assert failed.json()["providers"]["curve"]["error"]["message"] == (
                "Provider circuit is open"
            )
            healthy = client.get("/v1/price", params={**params, "chain_id": 1})
            assert healthy.json()["price_data"]["provider"] == "curve"
            quote = client.get(
                "/v1/quote",
                params={
                    "chain_id": 42161,
                    "token_in": token("USDC"),
                    "token_out": token("CRV"),
                    "amount_in": "1000000",
                },
            )
            assert quote.json()["quote"]["provider"] == "curve"
            assert client.get("/v1/ready").status_code == 200
            ethereum.respond(503)
            for _ in range(2):
                client.get("/v1/price", params={**params, "chain_id": 1})
            ready = client.get("/v1/ready")
            assert ready.status_code == 503
            assert ready.json()["checks"]["reason"] == "no_ready_price_providers"
            metrics = client.get("/metrics").text
            assert (
                'price_api_provider_circuit_state{chain_id="42161",operation="price",'
                'provider="curve"} 2.0'
            ) in metrics


def test_unsupported_provider_chain_does_not_fail_other_price_providers() -> None:
    address = token("USDC")
    with respx.mock(assert_all_called=True) as router:
        router.get(f"https://coins.llama.fi/prices/current/ethereum:{address}").respond(
            200, json={"coins": {f"ethereum:{address}": {"price": "1"}}}
        )
        with TestClient(app) as client:
            get_provider_registry()._plugins["curve"].enabled_chains = []
            response = client.get(
                "/v1/price", params={"token": address, "providers": "curve,defillama"}
            )
    assert response.status_code == 200
    payload = response.json()
    assert payload["price_data"]["provider"] == "defillama"
    assert payload["providers"]["curve"]["error"]["type"] == "UNSUPPORTED_CHAIN"
    assert payload["summary"]["successful_providers"] == 1
    assert payload["summary"]["failed_providers"] == 1


def test_unsupported_provider_chain_and_operation_do_not_fail_other_quoters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIFI_API_KEY", "fixture")
    with respx.mock(assert_all_called=True) as router:
        router.get("https://li.quest/v1/quote").respond(
            200, json={"estimate": {"toAmount": "1000000"}}
        )
        with TestClient(app) as client:
            get_provider_registry()._plugins["curve"].enabled_chains = []
            response = client.get(
                "/v1/quote",
                params={
                    "token_in": token("CRV"),
                    "token_out": token("USDC"),
                    "amount_in": "1000000000000000000",
                    "providers": "curve,defillama,lifi",
                },
            )
    assert response.status_code == 200
    payload = response.json()
    assert payload["quote"]["provider"] == "lifi"
    assert payload["providers"]["curve"]["error"]["type"] == "UNSUPPORTED_CHAIN"
    assert payload["providers"]["defillama"]["error"]["type"] == "UNSUPPORTED_OPERATION"
    assert payload["summary"]["successful_providers"] == 1
    assert payload["summary"]["failed_providers"] == 2


def test_enso_keeps_price_and_quote_requests_on_each_network_in_one_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAIN_IDS", ",".join(map(str, CHAINS)))
    monkeypatch.setenv("ENSO_API_KEY", "fixture")
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.enso.build/api/v1/account/accountId").respond(200)
        route = router.get("https://api.enso.build/api/v1/shortcuts/route").respond(
            200, json={"amountOut": "1000000"}
        )
        for chain_id in CHAINS:
            router.get(
                f"https://api.enso.finance/api/v1/prices/{chain_id}/{token('USDC')}"
            ).respond(200, json={"price": "1"})
        with TestClient(app) as client:
            for chain_id in CHAINS:
                price = client.get(
                    "/v1/price",
                    params={"chain_id": chain_id, "token": token("USDC"), "providers": "enso"},
                )
                assert price.status_code == 200
                assert price.json()["price_data"]["provider"] == "enso"
                quote = client.get(
                    "/v1/quote",
                    params={
                        "chain_id": chain_id,
                        "token_in": token("USDC"),
                        "token_out": token("CRV"),
                        "amount_in": "1000000",
                        "providers": "enso",
                    },
                )
                assert quote.status_code == 200
                assert quote.json()["quote"]["provider"] == "enso"
                assert route.calls.last.request.url.params["chainId"] == str(chain_id)


def test_vault_quote_converts_both_legs_on_each_network_in_one_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAIN_IDS", ",".join(map(str, CHAINS)))
    for chain in CHAINS.values():
        monkeypatch.setenv(chain.rpc_env, "https://rpc.example")
    vault_in, vault_out = token("CRV"), token("USDC")
    asset_in, asset_out = token("CVX"), token("YFI")
    resolver = get_vault_resolver()

    async def detect(address: str, chain_id: int) -> Erc4626VaultInfo:
        rate = list(CHAINS).index(chain_id) + 2 if address == vault_in else 4
        return Erc4626VaultInfo(
            vault_address=address,
            underlying_token=asset_in if address == vault_in else asset_out,
            share_decimals=18,
            underlying_decimals=18,
            assets_per_share_unit=rate * 10**18,
        )

    async def block_number(chain_id: int) -> int:
        return chain_id + 100

    monkeypatch.setattr(resolver._erc4626, "detect", detect)
    monkeypatch.setattr(resolver._rpc_client, "block_number", block_number)
    # Metadata enrichment is independent of the vault conversion under test.
    monkeypatch.setattr(
        "price_api.token_metadata.resolver.TokenMetadataResolver._fetch_onchain_metadata",
        AsyncMock(return_value={}),
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://www.curve.finance/api/router/v1/routes").respond(
            200, json={"amountOut": str(800 * 10**18), "amountOutMin": str(760 * 10**18)}
        )
        with TestClient(app) as client:
            for index, chain_id in enumerate(CHAINS):
                response = client.get(
                    "/v1/quote",
                    params={
                        "chain_id": chain_id,
                        "token_in": vault_in.lower(),
                        "token_out": vault_out.lower(),
                        "amount_in": str(100 * 10**18),
                        "providers": "curve",
                        "use_underlying": "true",
                    },
                )
                assert response.status_code == 200
                payload = response.json()
                assert payload["token_in"]["address"] == vault_in
                assert payload["token_out"]["address"] == vault_out
                quote = payload["quote"]
                assert quote["amount_in"] == str(100 * 10**18)
                assert quote["amount_out"] == str(200 * 10**18)
                assert quote["amount_out_min"] == str(190 * 10**18)
                context = quote["vault_context"]
                assert context["underlying_token_in"] == asset_in
                assert context["underlying_token_out"] == asset_out
                assert context["price_per_share_token_in"] == str(index + 2)
                assert context["price_per_share_token_out"] == "4"
                assert context["block_number"] == chain_id + 100
                params = route.calls.last.request.url.params
                assert params["chainId"] == str(chain_id)
                assert params["tokenIn"] == asset_in
                assert params["tokenOut"] == asset_out
                assert params["amountIn"] == str(100 * (index + 2) * 10**18)

from __future__ import annotations

from price_api.web3.client import AsyncRpcClient


def test_rpc_client_disables_hidden_provider_retries_and_applies_timeout() -> None:
    client = AsyncRpcClient(rpc_urls=["https://rpc.example"], request_timeout_s=0.25)
    provider = client._clients[0].provider

    assert provider.exception_retry_configuration is None
    assert provider.get_request_kwargs()["timeout"] == 0.25

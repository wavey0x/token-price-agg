from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from price_api.core.errors import InvalidRequestError
from price_api.web3.client import AsyncRpcClient


def test_rpc_client_disables_hidden_provider_retries_and_applies_timeout() -> None:
    client = AsyncRpcClient(rpc_urls={1: "https://rpc.example"}, request_timeout_s=0.25)
    provider = client._clients[1].provider

    assert provider.exception_retry_configuration is None
    assert provider.get_request_kwargs()["timeout"] == 0.25


class EthStub:
    def __init__(self, chain_id: int) -> None:
        self.chain = chain_id
        self.identity_reads = 0
        self.contract = MagicMock()
        self.contract.return_value.functions.decimals.return_value.call = AsyncMock(return_value=6)

    @property
    async def chain_id(self) -> int:
        self.identity_reads += 1
        return self.chain

    @property
    async def block_number(self) -> int:
        return 123


@pytest.mark.asyncio
async def test_rpc_uses_only_requested_chain_and_verifies_identity_once() -> None:
    rpc = AsyncRpcClient(rpc_urls={})
    ethereum, arbitrum = EthStub(1), EthStub(42161)
    rpc._clients = {1: SimpleNamespace(eth=ethereum), 42161: SimpleNamespace(eth=arbitrum)}  # type: ignore[dict-item]
    assert (
        await rpc.call(
            chain_id=42161, address="0x" + "11" * 20, abi=[], fn_name="decimals", args=[]
        )
        == 6
    )
    assert await rpc.block_number(42161) == 123
    assert arbitrum.identity_reads == 1
    assert ethereum.identity_reads == 0
    ethereum.contract.assert_not_called()


@pytest.mark.asyncio
async def test_missing_rpc_does_not_use_ethereum_as_fallback() -> None:
    rpc = AsyncRpcClient(rpc_urls={1: "https://rpc.example"})
    assert rpc.configured(1)
    assert not rpc.configured(42161)
    with pytest.raises(InvalidRequestError, match="No RPC configured for chain 42161"):
        await rpc.block_number(42161)
    await rpc.aclose()


@pytest.mark.asyncio
async def test_wrong_chain_rpc_fails_before_any_contract_read() -> None:
    rpc = AsyncRpcClient(rpc_urls={})
    wrong = EthStub(1)
    rpc._clients = {42161: SimpleNamespace(eth=wrong)}  # type: ignore[dict-item]
    with pytest.raises(InvalidRequestError) as caught:
        await rpc.call(
            chain_id=42161, address="0x" + "11" * 20, abi=[], fn_name="decimals", args=[]
        )
    assert caught.value.type == "RPC_CHAIN_MISMATCH"
    wrong.contract.assert_not_called()

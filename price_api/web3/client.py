from __future__ import annotations

from typing import Any

from eth_utils.address import to_checksum_address
from web3 import AsyncWeb3
from web3.providers.rpc import AsyncHTTPProvider

from price_api.core.errors import InvalidRequestError


class AsyncRpcClient:
    """One explicitly selected endpoint per chain; never fall back across networks."""

    def __init__(self, *, rpc_urls: dict[int, str], request_timeout_s: float = 1.5) -> None:
        self._clients: dict[int, AsyncWeb3[Any]] = {
            chain_id: AsyncWeb3(
                AsyncHTTPProvider(
                    url,
                    request_kwargs={"timeout": request_timeout_s},
                    exception_retry_configuration=None,
                )
            )
            for chain_id, url in rpc_urls.items()
        }
        self._verified: set[int] = set()

    def configured(self, chain_id: int) -> bool:
        return chain_id in self._clients

    async def _client(self, chain_id: int) -> AsyncWeb3[Any]:
        client = self._clients.get(chain_id)
        if client is None:
            raise InvalidRequestError(
                "RPC_NOT_CONFIGURED", f"No RPC configured for chain {chain_id}"
            )
        if chain_id not in self._verified:
            try:
                actual = await client.eth.chain_id
            except Exception as exc:
                raise InvalidRequestError(
                    "RPC_UNAVAILABLE", f"RPC unavailable for chain {chain_id}"
                ) from exc
            if actual != chain_id:
                raise InvalidRequestError(
                    "RPC_CHAIN_MISMATCH", f"RPC does not serve chain {chain_id}"
                )
            self._verified.add(chain_id)
        return client

    async def call(
        self,
        *,
        chain_id: int,
        address: str,
        abi: list[dict[str, Any]],
        fn_name: str,
        args: list[Any],
    ) -> Any:
        client = await self._client(chain_id)
        contract = client.eth.contract(address=to_checksum_address(address), abi=abi)
        return await getattr(contract.functions, fn_name)(*args).call()

    async def block_number(self, chain_id: int) -> int:
        client = await self._client(chain_id)
        return await client.eth.block_number

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.provider.disconnect()

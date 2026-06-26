from __future__ import annotations

from typing import Any

from eth_utils.address import to_checksum_address
from web3 import AsyncWeb3
from web3.providers.rpc import AsyncHTTPProvider


class AsyncRpcClient:
    def __init__(self, *, rpc_urls: list[str], request_timeout_s: float = 1.5) -> None:
        self._clients: list[AsyncWeb3[Any]] = [
            AsyncWeb3(AsyncHTTPProvider(url, request_kwargs={"timeout": request_timeout_s}))
            for url in rpc_urls
        ]

    def configured(self) -> bool:
        return len(self._clients) > 0

    async def call(
        self, *, address: str, abi: list[dict[str, Any]], fn_name: str, args: list[Any]
    ) -> Any:
        if not self._clients:
            raise RuntimeError("No RPC URLs configured")

        last_exc: Exception | None = None
        for client in self._clients:
            try:
                contract = client.eth.contract(address=to_checksum_address(address), abi=abi)
                function = getattr(contract.functions, fn_name)(*args)
                return await function.call()
            except Exception as exc:
                last_exc = exc
                continue

        if last_exc is None:
            raise RuntimeError("RPC call failed without exception")
        raise last_exc

    async def block_number(self) -> int:
        if not self._clients:
            raise RuntimeError("No RPC URLs configured")

        last_exc: Exception | None = None
        for client in self._clients:
            try:
                return await client.eth.block_number
            except Exception as exc:
                last_exc = exc
                continue

        if last_exc is None:
            raise RuntimeError("Failed to fetch block number")
        raise last_exc

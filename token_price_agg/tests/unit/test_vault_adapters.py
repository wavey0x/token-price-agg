from __future__ import annotations

import pytest
from web3 import Web3

from token_price_agg.vault.adapters.erc4626 import Erc4626Adapter
from token_price_agg.vault.adapters.yearn_v2 import YearnV2Adapter

_WEB3 = Web3()


class RpcStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._seq = 0

    async def call(
        self,
        *,
        address: str,
        abi: list[dict[str, object]],
        fn_name: str,
        args: list[object],
    ) -> object:
        self.calls.append((fn_name, address))
        if fn_name != "aggregate3":
            raise AssertionError(f"expected multicall aggregate3, got {fn_name}")

        self._seq += 1
        if self._seq == 1:
            return [
                (
                    True,
                    _WEB3.codec.encode(
                        ["address"], ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]
                    ),
                ),
                (True, _WEB3.codec.encode(["uint256"], [18])),
                (True, _WEB3.codec.encode(["uint256"], [2 * 10**18])),
            ]
        return [(True, _WEB3.codec.encode(["uint256"], [6]))]


class Erc4626RpcStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._seq = 0

    async def call(
        self,
        *,
        address: str,
        abi: list[dict[str, object]],
        fn_name: str,
        args: list[object],
    ) -> object:
        self.calls.append((fn_name, address))
        if fn_name != "aggregate3":
            raise AssertionError(f"expected multicall aggregate3, got {fn_name}")
        self._seq += 1
        if self._seq == 1:
            return [
                (
                    True,
                    _WEB3.codec.encode(
                        ["address"], ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]
                    ),
                ),
                (True, _WEB3.codec.encode(["uint256"], [18])),
            ]
        return [
            (True, _WEB3.codec.encode(["uint256"], [6])),
            (False, b""),
            (True, _WEB3.codec.encode(["uint256"], [15 * 10**17])),
        ]


@pytest.mark.asyncio
async def test_erc4626_adapter_uses_multicall_on_mainnet() -> None:
    rpc = Erc4626RpcStub()
    adapter = Erc4626Adapter(rpc_client=rpc)  # type: ignore[arg-type]
    info = await adapter.detect(
        "0x13db1cb418573f4c3a2ea36486f0e421bc0d2427",
        chain_id=1,
    )

    assert info is not None
    assert info.underlying_token == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    assert info.share_decimals == 18
    assert info.underlying_decimals == 6
    assert info.assets_per_share_unit == 15 * 10**17
    assert [name for name, _ in rpc.calls] == ["aggregate3", "aggregate3"]


@pytest.mark.asyncio
async def test_yearn_adapter_uses_multicall_on_mainnet() -> None:
    rpc = RpcStub()
    adapter = YearnV2Adapter(rpc_client=rpc)  # type: ignore[arg-type]
    info = await adapter.detect(
        "0x5f18c75abdae578b483e5f43f12a39cf75b973a9",
        chain_id=1,
    )

    assert info is not None
    assert info.underlying_token == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    assert info.share_decimals == 18
    assert info.underlying_decimals == 6
    assert info.price_per_share == 2 * 10**18
    assert [name for name, _ in rpc.calls] == ["aggregate3", "aggregate3"]

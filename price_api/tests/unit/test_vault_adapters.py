from __future__ import annotations

import pytest
from web3 import Web3

from price_api.core.errors import InvalidRequestError
from price_api.vault.adapters.common import decode_token_decimals
from price_api.vault.adapters.erc4626 import Erc4626Adapter
from price_api.vault.adapters.yearn_v2 import YearnV2Adapter

_WEB3 = Web3()


def test_token_decimals_decoder_accepts_normal_value_and_rejects_unsafe_values() -> None:
    assert decode_token_decimals(_WEB3.codec.encode(["uint256"], [18])) == 18
    assert decode_token_decimals(_WEB3.codec.encode(["uint256"], [78])) is None
    assert decode_token_decimals(_WEB3.codec.encode(["uint256"], [256])) is None
    assert decode_token_decimals(b"\x12") is None


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
                    _WEB3.codec.encode(["address"], ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]),
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
                    _WEB3.codec.encode(["address"], ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]),
                ),
                (True, _WEB3.codec.encode(["uint256"], [18])),
            ]
        return [
            (True, _WEB3.codec.encode(["uint256"], [6])),
            (False, b""),
            (True, _WEB3.codec.encode(["uint256"], [15 * 10**17])),
        ]


class NonVaultRpcStub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(
        self,
        *,
        address: str,
        abi: list[dict[str, object]],
        fn_name: str,
        args: list[object],
    ) -> object:
        del address, abi, args
        self.calls.append(fn_name)
        return [(False, b""), (False, b""), (False, b"")]


class FailingRpcStub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(
        self,
        *,
        address: str,
        abi: list[dict[str, object]],
        fn_name: str,
        args: list[object],
    ) -> object:
        del address, abi, args
        self.calls.append(fn_name)
        raise RuntimeError("rpc unavailable")


class MalformedErc4626RpcStub:
    async def call(
        self,
        *,
        address: str,
        abi: list[dict[str, object]],
        fn_name: str,
        args: list[object],
    ) -> object:
        del address, abi, args
        assert fn_name == "aggregate3"
        return [
            (
                True,
                _WEB3.codec.encode(["address"], ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]),
            ),
            (True, _WEB3.codec.encode(["uint256"], [256])),
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


@pytest.mark.asyncio
async def test_vault_adapters_treat_failed_interface_probe_as_non_vault() -> None:
    erc_rpc = NonVaultRpcStub()
    yearn_rpc = NonVaultRpcStub()

    erc_info = await Erc4626Adapter(rpc_client=erc_rpc).detect(  # type: ignore[arg-type]
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        chain_id=1,
    )
    yearn_info = await YearnV2Adapter(rpc_client=yearn_rpc).detect(  # type: ignore[arg-type]
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        chain_id=1,
    )

    assert erc_info is None
    assert yearn_info is None
    assert erc_rpc.calls == ["aggregate3"]
    assert yearn_rpc.calls == ["aggregate3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [Erc4626Adapter, YearnV2Adapter])
async def test_vault_adapters_propagate_rpc_failure(adapter_type: type[object]) -> None:
    rpc = FailingRpcStub()
    adapter = adapter_type(rpc_client=rpc)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError, match="rpc unavailable"):
        await adapter.detect(  # type: ignore[attr-defined]
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            chain_id=1,
        )

    assert rpc.calls[0] == "aggregate3"


@pytest.mark.asyncio
async def test_erc4626_adapter_rejects_malformed_vault_data() -> None:
    adapter = Erc4626Adapter(rpc_client=MalformedErc4626RpcStub())  # type: ignore[arg-type]

    with pytest.raises(InvalidRequestError) as exc_info:
        await adapter.detect(
            "0x13db1cb418573f4c3a2ea36486f0e421bc0d2427",
            chain_id=1,
        )

    assert exc_info.value.type == "INVALID_VAULT_DATA"

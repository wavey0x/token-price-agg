from __future__ import annotations

from collections import defaultdict
from typing import Any

from web3 import Web3

from token_price_agg.core.models import TokenMetadata
from token_price_agg.web3.client import AsyncRpcClient

_WEB3 = Web3()

_MULTICALL3_BY_CHAIN = {
    1: Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11"),
}

_MULTICALL3_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"},
                ],
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]

_ERC20_DECIMALS_SELECTOR = bytes.fromhex("313ce567")
_ERC20_SYMBOL_SELECTOR = bytes.fromhex("95d89b41")


async def fetch_onchain_metadata(
    *,
    chain_id: int,
    addresses: list[str],
    rpc_client: AsyncRpcClient,
) -> dict[str, TokenMetadata]:
    if not addresses or not rpc_client.configured():
        return {}

    multicall_address = _MULTICALL3_BY_CHAIN.get(chain_id)
    if multicall_address is None:
        return {}

    calls: list[tuple[str, bool, bytes]] = []
    call_index: list[tuple[str, str]] = []
    for address in addresses:
        checksum = Web3.to_checksum_address(address)
        calls.append((checksum, True, _ERC20_DECIMALS_SELECTOR))
        call_index.append((address, "decimals"))
        calls.append((checksum, True, _ERC20_SYMBOL_SELECTOR))
        call_index.append((address, "symbol"))

    raw = await rpc_client.call(
        address=multicall_address,
        abi=_MULTICALL3_ABI,
        fn_name="aggregate3",
        args=[calls],
    )

    decoded = normalize_multicall_result(raw)
    out: dict[str, dict[str, Any]] = defaultdict(dict)

    for index, item in enumerate(decoded):
        if index >= len(call_index):
            break

        address, field = call_index[index]
        success, return_data = item
        if not success or return_data is None:
            continue

        if field == "decimals":
            decimals = decode_uint8(return_data)
            if decimals is not None:
                out[address]["decimals"] = decimals
            continue

        symbol = decode_symbol(return_data)
        if symbol is not None:
            out[address]["symbol"] = symbol

    result: dict[str, TokenMetadata] = {}
    for address, payload in out.items():
        result[address] = TokenMetadata(
            chain_id=chain_id,
            address=address,
            is_native=False,
            symbol=payload.get("symbol"),
            decimals=payload.get("decimals"),
            logo_url=None,
            source="onchain_multicall",
        )
    return result


def normalize_multicall_result(value: object) -> list[tuple[bool, bytes | None]]:
    if not isinstance(value, (list, tuple)):
        return []

    normalized: list[tuple[bool, bytes | None]] = []
    for item in value:
        success: bool | None = None
        data: object | None = None

        if isinstance(item, dict):
            success = bool(item.get("success"))
            data = item.get("returnData")
        elif isinstance(item, (list, tuple)):
            if len(item) >= 2:
                success = bool(item[0])
                data = item[1]

        if success is None:
            continue

        if data is None:
            normalized.append((success, None))
            continue
        if isinstance(data, str):
            stripped = data[2:] if data.startswith("0x") else data
            try:
                normalized.append((success, bytes.fromhex(stripped)))
            except ValueError:
                normalized.append((success, None))
            continue
        if isinstance(data, (bytes, bytearray)):
            normalized.append((success, bytes(data)))
            continue

        normalized.append((success, None))

    return normalized


def decode_uint8(data: bytes) -> int | None:
    try:
        value = int(_WEB3.codec.decode(["uint8"], data)[0])
    except Exception:
        return None
    if value < 0 or value > 255:
        return None
    return value


def decode_symbol(data: bytes) -> str | None:
    try:
        symbol = str(_WEB3.codec.decode(["string"], data)[0]).strip()
        if symbol:
            return symbol
    except Exception:
        pass

    try:
        raw = _WEB3.codec.decode(["bytes32"], data)[0]
    except Exception:
        return None

    if not isinstance(raw, bytes):
        return None
    parsed = raw.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
    return parsed or None

from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3

from token_price_agg.vault.adapters.common import load_abi
from token_price_agg.web3.client import AsyncRpcClient

_ERC20_ABI = load_abi("erc20.json")
_ERC4626_ABI = load_abi("erc4626.json")
_MULTICALL3_ABI = load_abi("multicall3.json")
_MULTICALL3_BY_CHAIN: dict[int, str] = {
    1: Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11"),
}

_ERC4626_ASSET_SELECTOR = bytes.fromhex("38d52e0f")
_ERC20_DECIMALS_SELECTOR = bytes.fromhex("313ce567")
_ERC4626_CONVERT_TO_ASSETS_SELECTOR = bytes.fromhex("07a2d13a")
_ERC4626_PREVIEW_REDEEM_SELECTOR = bytes.fromhex("4cdad506")
_WEB3 = Web3()


@dataclass(frozen=True)
class Erc4626VaultInfo:
    vault_address: str
    underlying_token: str
    share_decimals: int
    underlying_decimals: int
    assets_per_share_unit: int

    def convert_shares_to_assets(self, shares: int) -> int:
        return int((shares * self.assets_per_share_unit) // (10**self.share_decimals))

    def share_to_asset_rate_str(self) -> str:
        return f"{self.assets_per_share_unit}/{10**self.underlying_decimals}"


class Erc4626Adapter:
    def __init__(self, rpc_client: AsyncRpcClient) -> None:
        self._rpc_client = rpc_client

    async def detect(self, vault_address: str, chain_id: int) -> Erc4626VaultInfo | None:
        try:
            multicall = await self._detect_with_multicall(
                vault_address=vault_address, chain_id=chain_id
            )
            if multicall is not None:
                return multicall
        except Exception:
            # Fallback to single-call detection path.
            pass

        try:
            underlying = await self._rpc_client.call(
                address=vault_address,
                abi=_ERC4626_ABI,
                fn_name="asset",
                args=[],
            )
            share_decimals_raw = await self._rpc_client.call(
                address=vault_address,
                abi=_ERC4626_ABI,
                fn_name="decimals",
                args=[],
            )
            share_decimals = int(share_decimals_raw)
            underlying_decimals_raw = await self._rpc_client.call(
                address=str(underlying),
                abi=_ERC20_ABI,
                fn_name="decimals",
                args=[],
            )
            one_share = 10**share_decimals
            try:
                assets_per_share = await self._rpc_client.call(
                    address=vault_address,
                    abi=_ERC4626_ABI,
                    fn_name="convertToAssets",
                    args=[one_share],
                )
            except Exception:
                assets_per_share = await self._rpc_client.call(
                    address=vault_address,
                    abi=_ERC4626_ABI,
                    fn_name="previewRedeem",
                    args=[one_share],
                )

            return Erc4626VaultInfo(
                vault_address=vault_address,
                underlying_token=str(underlying),
                share_decimals=share_decimals,
                underlying_decimals=int(underlying_decimals_raw),
                assets_per_share_unit=int(assets_per_share),
            )
        except Exception:
            return None

    async def _detect_with_multicall(
        self, *, vault_address: str, chain_id: int
    ) -> Erc4626VaultInfo | None:
        multicall_address = _MULTICALL3_BY_CHAIN.get(chain_id)
        if multicall_address is None:
            return None

        checksum = Web3.to_checksum_address(vault_address)
        calls: list[tuple[str, bool, bytes]] = [
            (checksum, True, _ERC4626_ASSET_SELECTOR),
            (checksum, True, _ERC20_DECIMALS_SELECTOR),
        ]
        raw = await self._rpc_client.call(
            address=multicall_address,
            abi=_MULTICALL3_ABI,
            fn_name="aggregate3",
            args=[calls],
        )
        decoded = _normalize_multicall_result(raw)
        if len(decoded) < 2:
            return None

        asset_success, asset_data = decoded[0]
        decimals_success, decimals_data = decoded[1]
        if not asset_success or not decimals_success or asset_data is None or decimals_data is None:
            return None

        underlying = _decode_address(asset_data)
        share_decimals = _decode_uint256(decimals_data)
        if underlying is None or share_decimals is None or share_decimals < 0:
            return None

        one_share = 10**share_decimals
        convert_data = _ERC4626_CONVERT_TO_ASSETS_SELECTOR + one_share.to_bytes(32, "big")
        fallback_data = _ERC4626_PREVIEW_REDEEM_SELECTOR + one_share.to_bytes(32, "big")
        conversion_raw = await self._rpc_client.call(
            address=multicall_address,
            abi=_MULTICALL3_ABI,
            fn_name="aggregate3",
            args=[
                [
                    (underlying, True, _ERC20_DECIMALS_SELECTOR),
                    (checksum, True, convert_data),
                    (checksum, True, fallback_data),
                ]
            ],
        )
        conversion = _normalize_multicall_result(conversion_raw)
        if len(conversion) < 3:
            return None

        underlying_decimals_success, underlying_decimals_data = conversion[0]
        if not underlying_decimals_success or underlying_decimals_data is None:
            return None
        underlying_decimals = _decode_uint256(underlying_decimals_data)
        if underlying_decimals is None or underlying_decimals < 0:
            return None

        assets_per_share: int | None = None
        for success, data in conversion[1:]:
            if not success or data is None:
                continue
            candidate = _decode_uint256(data)
            if candidate is not None:
                assets_per_share = candidate
                break
        if assets_per_share is None:
            return None

        return Erc4626VaultInfo(
            vault_address=vault_address,
            underlying_token=underlying,
            share_decimals=share_decimals,
            underlying_decimals=underlying_decimals,
            assets_per_share_unit=assets_per_share,
        )


def _normalize_multicall_result(value: object) -> list[tuple[bool, bytes | None]]:
    if not isinstance(value, (list, tuple)):
        return []
    normalized: list[tuple[bool, bytes | None]] = []
    for item in value:
        if isinstance(item, dict):
            success = bool(item.get("success"))
            data = item.get("returnData")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            success = bool(item[0])
            data = item[1]
        else:
            continue

        if data is None:
            normalized.append((success, None))
        elif isinstance(data, str):
            stripped = data[2:] if data.startswith("0x") else data
            try:
                normalized.append((success, bytes.fromhex(stripped)))
            except ValueError:
                normalized.append((success, None))
        elif isinstance(data, (bytes, bytearray)):
            normalized.append((success, bytes(data)))
        else:
            normalized.append((success, None))
    return normalized


def _decode_address(data: bytes) -> str | None:
    try:
        value = _WEB3.codec.decode(["address"], data)[0]
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    try:
        return Web3.to_checksum_address(value)
    except Exception:
        return None


def _decode_uint256(data: bytes) -> int | None:
    try:
        value = int(_WEB3.codec.decode(["uint256"], data)[0])
    except Exception:
        return None
    return value if value >= 0 else None

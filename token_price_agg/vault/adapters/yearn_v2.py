from __future__ import annotations

from dataclasses import dataclass

from token_price_agg.vault.adapters.common import load_abi
from token_price_agg.web3.client import AsyncRpcClient

_YEARN_V2_ABI = load_abi("yearn_v2_vault.json")


@dataclass(frozen=True)
class YearnV2VaultInfo:
    vault_address: str
    underlying_token: str
    share_decimals: int
    price_per_share: int

    def convert_shares_to_assets(self, shares: int) -> int:
        return int((shares * self.price_per_share) // (10**self.share_decimals))

    def share_to_asset_rate_str(self) -> str:
        return f"{self.price_per_share}/{10**self.share_decimals}"


class YearnV2Adapter:
    def __init__(self, rpc_client: AsyncRpcClient) -> None:
        self._rpc_client = rpc_client

    async def detect(self, vault_address: str) -> YearnV2VaultInfo | None:
        try:
            underlying = await self._rpc_client.call(
                address=vault_address,
                abi=_YEARN_V2_ABI,
                fn_name="token",
                args=[],
            )
            share_decimals_raw = await self._rpc_client.call(
                address=vault_address,
                abi=_YEARN_V2_ABI,
                fn_name="decimals",
                args=[],
            )
            pps_raw = await self._rpc_client.call(
                address=vault_address,
                abi=_YEARN_V2_ABI,
                fn_name="pricePerShare",
                args=[],
            )
            return YearnV2VaultInfo(
                vault_address=vault_address,
                underlying_token=str(underlying),
                share_decimals=int(share_decimals_raw),
                price_per_share=int(pps_raw),
            )
        except Exception:
            return None

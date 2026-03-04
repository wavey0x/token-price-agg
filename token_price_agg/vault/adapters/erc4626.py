from __future__ import annotations

from dataclasses import dataclass

from token_price_agg.vault.adapters.common import load_abi
from token_price_agg.web3.client import AsyncRpcClient

_ERC4626_ABI = load_abi("erc4626.json")


@dataclass(frozen=True)
class Erc4626VaultInfo:
    vault_address: str
    underlying_token: str
    share_decimals: int
    assets_per_share_unit: int

    def convert_shares_to_assets(self, shares: int) -> int:
        return int((shares * self.assets_per_share_unit) // (10**self.share_decimals))

    def share_to_asset_rate_str(self) -> str:
        return f"{self.assets_per_share_unit}/{10**self.share_decimals}"


class Erc4626Adapter:
    def __init__(self, rpc_client: AsyncRpcClient) -> None:
        self._rpc_client = rpc_client

    async def detect(self, vault_address: str) -> Erc4626VaultInfo | None:
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
                assets_per_share_unit=int(assets_per_share),
            )
        except Exception:
            return None

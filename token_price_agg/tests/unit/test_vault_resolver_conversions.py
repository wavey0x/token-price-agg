from token_price_agg.vault.adapters.erc4626 import Erc4626VaultInfo
from token_price_agg.vault.adapters.yearn_v2 import YearnV2VaultInfo
from token_price_agg.vault.resolver import _VaultInfo


def test_erc4626_assets_to_shares_respects_share_decimals() -> None:
    vault = _VaultInfo.from_erc4626(
        Erc4626VaultInfo(
            vault_address="0xBe53A109B494E5c9f97b9Cd39Fe969BE68BF6204",
            underlying_token="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            share_decimals=6,
            assets_per_share_unit=1_098_368,
        )
    )

    # If one full share (1e6 base units) maps to 1_098_368 assets,
    # converting those assets back must return 1e6 share base units.
    assert vault.convert_assets_to_shares(1_098_368) == 1_000_000


def test_yearn_assets_to_shares_respects_share_decimals() -> None:
    vault = _VaultInfo.from_yearn_v2(
        YearnV2VaultInfo(
            vault_address="0x1111111111111111111111111111111111111111",
            underlying_token="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            share_decimals=18,
            price_per_share=1_500_000,
        )
    )

    # 1_500_000 assets correspond to exactly one full share (1e18).
    assert vault.convert_assets_to_shares(1_500_000) == 10**18

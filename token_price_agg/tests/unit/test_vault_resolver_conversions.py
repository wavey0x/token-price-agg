from decimal import Decimal
from typing import Any, cast

import pytest

import token_price_agg.vault.resolver as resolver_module
from token_price_agg.app.config import Settings
from token_price_agg.vault.adapters.erc4626 import Erc4626VaultInfo
from token_price_agg.vault.adapters.yearn_v2 import YearnV2VaultInfo
from token_price_agg.vault.resolver import VaultResolver, _VaultInfo


def test_erc4626_assets_to_shares_respects_share_decimals() -> None:
    vault = _VaultInfo.from_erc4626(
        Erc4626VaultInfo(
            vault_address="0xBe53A109B494E5c9f97b9Cd39Fe969BE68BF6204",
            underlying_token="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            share_decimals=6,
            underlying_decimals=6,
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
            underlying_decimals=6,
            price_per_share=1_500_000,
        )
    )

    # 1_500_000 assets correspond to exactly one full share (1e18).
    assert vault.convert_assets_to_shares(1_500_000) == 10**18


def test_erc4626_price_per_share_uses_underlying_decimals() -> None:
    vault = _VaultInfo.from_erc4626(
        Erc4626VaultInfo(
            vault_address="0x01Ba69727E2860b37bc1a2bd56999c1aFb4C15D8",
            underlying_token="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            share_decimals=18,
            underlying_decimals=6,
            assets_per_share_unit=1_049_479,
        )
    )

    assert vault.price_per_share == Decimal("1.049479")


def test_yearn_price_per_share_uses_underlying_decimals() -> None:
    vault = _VaultInfo.from_yearn_v2(
        YearnV2VaultInfo(
            vault_address="0x1111111111111111111111111111111111111111",
            underlying_token="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            share_decimals=18,
            underlying_decimals=6,
            price_per_share=1_500_000,
        )
    )

    assert vault.price_per_share == Decimal("1.5")


@pytest.mark.asyncio
async def test_vault_resolver_reuses_positive_detection_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, resolver_module).time, "monotonic", lambda: now)
    resolver = VaultResolver(
        Settings(
            rpc_urls=["https://rpc.example"],
            vault_positive_cache_ttl_s=30,
            vault_negative_cache_ttl_s=300,
        )
    )
    erc4626 = _FakeErc4626Adapter(
        Erc4626VaultInfo(
            vault_address="0xBe53A109B494E5c9f97b9Cd39Fe969BE68BF6204",
            underlying_token="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            share_decimals=6,
            underlying_decimals=6,
            assets_per_share_unit=1_000_000,
        )
    )
    yearn = _FakeYearnAdapter(None)
    cast(Any, resolver)._erc4626 = erc4626
    cast(Any, resolver)._yearn_v2 = yearn

    first = await resolver._detect_vault(
        "0xBe53A109B494E5c9f97b9Cd39Fe969BE68BF6204",
        1,
    )
    second = await resolver._detect_vault(
        "0xbe53a109b494e5c9f97b9cd39fe969be68bf6204",
        1,
    )

    assert first is not None
    assert second is first
    assert erc4626.calls == 1
    assert yearn.calls == 0


@pytest.mark.asyncio
async def test_vault_resolver_reuses_negative_detection_cache_until_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(cast(Any, resolver_module).time, "monotonic", lambda: now)
    resolver = VaultResolver(
        Settings(
            rpc_urls=["https://rpc.example"],
            vault_positive_cache_ttl_s=30,
            vault_negative_cache_ttl_s=10,
        )
    )
    erc4626 = _FakeErc4626Adapter(None)
    yearn = _FakeYearnAdapter(None)
    cast(Any, resolver)._erc4626 = erc4626
    cast(Any, resolver)._yearn_v2 = yearn

    first = await resolver._detect_vault(
        "0x0000000000000000000000000000000000000001",
        1,
    )
    second = await resolver._detect_vault(
        "0x0000000000000000000000000000000000000001",
        1,
    )
    now = 1011.0
    third = await resolver._detect_vault(
        "0x0000000000000000000000000000000000000001",
        1,
    )

    assert first is None
    assert second is None
    assert third is None
    assert erc4626.calls == 2
    assert yearn.calls == 2


class _FakeErc4626Adapter:
    def __init__(self, result: Erc4626VaultInfo | None) -> None:
        self._result = result
        self.calls = 0

    async def detect(self, address: str, chain_id: int) -> Erc4626VaultInfo | None:
        self.calls += 1
        return self._result


class _FakeYearnAdapter:
    def __init__(self, result: YearnV2VaultInfo | None) -> None:
        self._result = result
        self.calls = 0

    async def detect(self, address: str, chain_id: int) -> YearnV2VaultInfo | None:
        self.calls += 1
        return self._result

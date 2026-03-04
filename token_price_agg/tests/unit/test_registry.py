from __future__ import annotations

from token_price_agg.app.config import Settings
from token_price_agg.providers.registry import ProviderRegistry


def test_missing_api_keys_mark_providers_unavailable() -> None:
    settings = Settings(
        lifi_api_key=None,
        enso_api_key=None,
        providers_enabled=["lifi", "enso"],
    )
    registry = ProviderRegistry(settings)

    capabilities = {item.id: item for item in registry.capabilities()}

    assert capabilities["lifi"].available is False
    assert capabilities["lifi"].unavailable_reason == "missing_api_key"
    assert capabilities["enso"].available is False
    assert capabilities["enso"].unavailable_reason == "missing_api_key"


def test_providers_enabled_controls_built_plugins() -> None:
    settings = Settings(providers_enabled=["curve"])
    registry = ProviderRegistry(settings)

    capabilities = [item.id for item in registry.capabilities()]
    assert capabilities == ["curve"]


def test_default_providers_enabled_are_built() -> None:
    settings = Settings(lifi_api_key="x", enso_api_key="y")
    registry = ProviderRegistry(settings)

    capabilities = sorted(item.id for item in registry.capabilities())
    assert capabilities == ["curve", "defillama", "enso", "lifi"]

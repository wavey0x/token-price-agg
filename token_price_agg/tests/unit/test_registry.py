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
    assert capabilities == ["curve", "defillama", "enso", "lifi", "odos"]


def test_provider_http_client_does_not_trust_env_by_default() -> None:
    settings = Settings(providers_enabled=["curve"])
    registry = ProviderRegistry(settings)

    assert registry._http_clients["curve"].trust_env is False


def test_provider_http_client_can_trust_env_when_enabled() -> None:
    settings = Settings(providers_enabled=["curve"], provider_http_trust_env=True)
    registry = ProviderRegistry(settings)

    assert registry._http_clients["curve"].trust_env is True


def test_provider_http_client_limits_follow_per_provider_config() -> None:
    settings = Settings(
        providers_enabled=["curve"],
        provider_global_limit=77,
        provider_max_connections_per_provider=11,
        provider_max_keepalive_connections_per_provider=3,
        provider_keepalive_expiry_s=4.0,
    )
    registry = ProviderRegistry(settings)
    client = registry._http_clients["curve"]

    assert client.max_connections == 11
    assert client.max_keepalive_connections == 3
    assert client.keepalive_expiry_s == 4.0


def test_each_provider_gets_an_isolated_http_client() -> None:
    settings = Settings(
        providers_enabled=["curve", "defillama"],
        provider_global_limit=77,
    )
    registry = ProviderRegistry(settings)

    assert set(registry._http_clients) == {"curve", "defillama"}
    assert registry._http_clients["curve"] is not registry._http_clients["defillama"]

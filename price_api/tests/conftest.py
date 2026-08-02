from __future__ import annotations

import os
from pathlib import Path

import pytest

from price_api.app.config import get_settings
from price_api.app.dependencies import (
    get_aggregator_service,
    get_anonymous_rate_limiter,
    get_api_key_store,
    get_provider_registry,
    get_token_metadata_resolver,
    get_vault_resolver,
)


@pytest.fixture(autouse=True)
def _clear_cached_singletons() -> None:
    get_settings.cache_clear()
    get_api_key_store.cache_clear()
    get_anonymous_rate_limiter.cache_clear()
    get_provider_registry.cache_clear()
    get_token_metadata_resolver.cache_clear()
    get_vault_resolver.cache_clear()
    get_aggregator_service.cache_clear()


@pytest.fixture(autouse=True)
def _set_default_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHAIN_IDS", "1")
    monkeypatch.setenv("PROVIDER_REQUEST_TIMEOUT_MS", "500")
    monkeypatch.setenv("PROVIDER_MAX_RETRIES", "0")
    monkeypatch.setenv("RPC_URLS", "")
    monkeypatch.setenv("APP_VERSION", "test")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("ENABLE_READINESS_STRICT", "false")
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "false")
    monkeypatch.setenv("API_KEY_RATE_LIMIT_RPM", "300")
    monkeypatch.setenv("API_KEY_UNAUTH_ACCESS_ENABLED", "true")
    monkeypatch.setenv("API_KEY_UNAUTH_MIN_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("API_KEY_DB_PATH", str(tmp_path / "api_keys.sqlite3"))
    monkeypatch.setenv("TOKEN_METADATA_DB_PATH", str(tmp_path / "token_metadata.sqlite3"))
    monkeypatch.setenv("TOKEN_LOGO_REFRESH_ON_STARTUP", "false")
    monkeypatch.setenv("PROVIDERS_ENABLED", "defillama,curve,lifi,enso")
    # Explicitly override .env values so tests can assert missing-key behavior.
    monkeypatch.setenv("LIFI_API_KEY", "")
    monkeypatch.setenv("ENSO_API_KEY", "")


@pytest.fixture(autouse=True)
def _disable_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]:
        if name in os.environ:
            monkeypatch.delenv(name, raising=False)

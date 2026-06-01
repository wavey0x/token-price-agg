from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from token_price_agg.app.config import Settings


def test_settings_loads_toml_when_env_overrides_absent(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.toml").write_text(
        "\n".join(
            [
                "[timeouts]",
                "provider_request_timeout_ms = 650",
                "provider_max_retries = 0",
                "provider_http_trust_env = true",
                "",
                "[concurrency]",
                "provider_fanout_per_request = 5",
                "provider_global_limit = 99",
                "provider_global_units = 42",
                "provider_per_principal_units = 7",
                "provider_principal_limiter_idle_ttl_s = 321",
                "provider_principal_limiter_max_entries = 1234",
                "provider_per_provider_units = 11",
                "vault_global_units = 13",
                "admission_acquire_timeout_ms = 9",
                "",
                "[transport]",
                "provider_pool_timeout_ms = 20",
                "provider_connect_timeout_ms = 300",
                "provider_read_timeout_ms = 700",
                "provider_write_timeout_ms = 400",
                "provider_max_connections_per_provider = 17",
                "provider_max_keepalive_connections_per_provider = 3",
                "provider_keepalive_expiry_s = 1.5",
                "provider_client_ttl_s = 60",
                "provider_client_max_requests = 1000",
                "provider_recycle_pool_timeout_threshold = 4",
                "provider_recycle_window_s = 12",
                "",
                "[circuit_breakers]",
                "failure_window_s = 10",
                "failure_threshold = 3",
                "open_duration_s = 4",
                "half_open_probe_count = 1",
                "",
                "[vault]",
                "positive_cache_ttl_s = 15",
                "negative_cache_ttl_s = 90",
                "",
                "[readiness]",
                "close_wait_ready_threshold = 12",
                "",
                "[providers]",
                'enabled = ["curve", "defillama"]',
                'price_priority = ["curve"]',
                'quote_priority = ["curve"]',
                "",
                "[providers.lifi]",
                'deny_exchanges = ["fly"]',
                "",
                "[chains]",
                "ids = [1, 10]",
                "",
                "[rpc]",
                'urls = ["https://rpc.1.example", "https://rpc.10.example"]',
                "",
                "[security]",
                "api_key_auth_enabled = true",
                'api_key_db_path = "data/custom_api_keys.sqlite3"',
                "api_key_rate_limit_rpm = 123",
                "api_key_unauth_access_enabled = true",
                "api_key_unauth_min_interval_seconds = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    for key in [
        "PROVIDER_REQUEST_TIMEOUT_MS",
        "PROVIDER_MAX_RETRIES",
        "PROVIDER_HTTP_TRUST_ENV",
        "PROVIDER_FANOUT_PER_REQUEST",
        "PROVIDER_GLOBAL_LIMIT",
        "PROVIDER_GLOBAL_UNITS",
        "PROVIDER_PER_PRINCIPAL_UNITS",
        "PROVIDER_PRINCIPAL_LIMITER_IDLE_TTL_S",
        "PROVIDER_PRINCIPAL_LIMITER_MAX_ENTRIES",
        "PROVIDER_PER_PROVIDER_UNITS",
        "VAULT_GLOBAL_UNITS",
        "ADMISSION_ACQUIRE_TIMEOUT_MS",
        "PROVIDER_POOL_TIMEOUT_MS",
        "PROVIDER_CONNECT_TIMEOUT_MS",
        "PROVIDER_READ_TIMEOUT_MS",
        "PROVIDER_WRITE_TIMEOUT_MS",
        "PROVIDER_MAX_CONNECTIONS_PER_PROVIDER",
        "PROVIDER_MAX_KEEPALIVE_CONNECTIONS_PER_PROVIDER",
        "PROVIDER_KEEPALIVE_EXPIRY_S",
        "PROVIDER_CLIENT_TTL_S",
        "PROVIDER_CLIENT_MAX_REQUESTS",
        "PROVIDER_RECYCLE_POOL_TIMEOUT_THRESHOLD",
        "PROVIDER_RECYCLE_WINDOW_S",
        "PROVIDER_CIRCUIT_FAILURE_WINDOW_S",
        "PROVIDER_CIRCUIT_FAILURE_THRESHOLD",
        "PROVIDER_CIRCUIT_OPEN_DURATION_S",
        "PROVIDER_CIRCUIT_HALF_OPEN_PROBE_COUNT",
        "VAULT_POSITIVE_CACHE_TTL_S",
        "VAULT_NEGATIVE_CACHE_TTL_S",
        "CLOSE_WAIT_READY_THRESHOLD",
        "CHAIN_IDS",
        "RPC_URLS",
        "PROVIDERS_ENABLED",
        "PRICE_PROVIDER_PRIORITY",
        "QUOTE_PROVIDER_PRIORITY",
        "LIFI_DENY_EXCHANGES",
        "API_KEY_AUTH_ENABLED",
        "API_KEY_DB_PATH",
        "API_KEY_RATE_LIMIT_RPM",
        "API_KEY_UNAUTH_ACCESS_ENABLED",
        "API_KEY_UNAUTH_MIN_INTERVAL_SECONDS",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.provider_request_timeout_ms == 650
    assert settings.provider_max_retries == 0
    assert settings.provider_http_trust_env is True
    assert settings.provider_fanout_per_request == 5
    assert settings.provider_global_limit == 99
    assert settings.provider_global_units == 42
    assert settings.effective_provider_global_units == 42
    assert settings.provider_per_principal_units == 7
    assert settings.provider_principal_limiter_idle_ttl_s == 321
    assert settings.provider_principal_limiter_max_entries == 1234
    assert settings.provider_per_provider_units == 11
    assert settings.vault_global_units == 13
    assert settings.admission_acquire_timeout_ms == 9
    assert settings.provider_pool_timeout_ms == 20
    assert settings.provider_connect_timeout_ms == 300
    assert settings.provider_read_timeout_ms == 700
    assert settings.effective_provider_read_timeout_ms == 700
    assert settings.provider_write_timeout_ms == 400
    assert settings.provider_max_connections_per_provider == 17
    assert settings.effective_provider_max_connections_per_provider == 17
    assert settings.provider_max_keepalive_connections_per_provider == 3
    assert settings.provider_keepalive_expiry_s == 1.5
    assert settings.provider_client_ttl_s == 60
    assert settings.provider_client_max_requests == 1000
    assert settings.provider_recycle_pool_timeout_threshold == 4
    assert settings.provider_recycle_window_s == 12
    assert settings.provider_circuit_failure_window_s == 10
    assert settings.provider_circuit_failure_threshold == 3
    assert settings.provider_circuit_open_duration_s == 4
    assert settings.provider_circuit_half_open_probe_count == 1
    assert settings.vault_positive_cache_ttl_s == 15
    assert settings.vault_negative_cache_ttl_s == 90
    assert settings.close_wait_ready_threshold == 12
    assert settings.chain_ids == [1, 10]
    assert settings.rpc_urls == ["https://rpc.1.example", "https://rpc.10.example"]
    assert settings.providers_enabled == ["curve", "defillama"]
    assert settings.price_provider_priority == ["curve"]
    assert settings.quote_provider_priority == ["curve"]
    assert settings.lifi_deny_exchanges == ["fly"]
    assert settings.api_key_auth_enabled is True
    assert settings.api_key_db_path == "data/custom_api_keys.sqlite3"
    assert settings.api_key_rate_limit_rpm == 123
    assert settings.api_key_unauth_access_enabled is True
    assert settings.api_key_unauth_min_interval_seconds == 1
    assert settings.aggregate_price_deadline_ms == 750
    assert settings.aggregate_quote_deadline_ms == 950


def test_providers_enabled_can_be_overridden_directly() -> None:
    settings = Settings(providers_enabled=["curve"])
    assert settings.providers_enabled == ["curve"]


def test_provider_http_trust_env_defaults_false() -> None:
    settings = Settings()
    assert settings.provider_http_trust_env is False


def test_lifi_deny_exchanges_can_be_set_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("LIFI_DENY_EXCHANGES", "fly, okx, fly")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.lifi_deny_exchanges == ["fly", "okx"]


def test_api_key_rate_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="API_KEY_RATE_LIMIT_RPM must be > 0"):
        Settings(api_key_rate_limit_rpm=0)


def test_api_key_unauth_min_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="API_KEY_UNAUTH_MIN_INTERVAL_SECONDS must be > 0"):
        Settings(api_key_unauth_min_interval_seconds=0)

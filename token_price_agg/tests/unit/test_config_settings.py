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
                "",
                "[concurrency]",
                "provider_fanout_per_request = 5",
                "provider_global_limit = 99",
                "",
                "[providers]",
                'enabled = ["curve", "defillama"]',
                'price_priority = ["curve"]',
                'quote_priority = ["curve"]',
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
                "api_key_unauth_rate_limit_rps = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    for key in [
        "PROVIDER_REQUEST_TIMEOUT_MS",
        "PROVIDER_MAX_RETRIES",
        "PROVIDER_FANOUT_PER_REQUEST",
        "PROVIDER_GLOBAL_LIMIT",
        "CHAIN_IDS",
        "RPC_URLS",
        "PROVIDERS_ENABLED",
        "PRICE_PROVIDER_PRIORITY",
        "QUOTE_PROVIDER_PRIORITY",
        "API_KEY_AUTH_ENABLED",
        "API_KEY_DB_PATH",
        "API_KEY_RATE_LIMIT_RPM",
        "API_KEY_UNAUTH_ACCESS_ENABLED",
        "API_KEY_UNAUTH_RATE_LIMIT_RPS",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.provider_request_timeout_ms == 650
    assert settings.provider_max_retries == 0
    assert settings.provider_fanout_per_request == 5
    assert settings.provider_global_limit == 99
    assert settings.chain_ids == [1, 10]
    assert settings.rpc_urls == ["https://rpc.1.example", "https://rpc.10.example"]
    assert settings.providers_enabled == ["curve", "defillama"]
    assert settings.price_provider_priority == ["curve"]
    assert settings.quote_provider_priority == ["curve"]
    assert settings.api_key_auth_enabled is True
    assert settings.api_key_db_path == "data/custom_api_keys.sqlite3"
    assert settings.api_key_rate_limit_rpm == 123
    assert settings.api_key_unauth_access_enabled is True
    assert settings.api_key_unauth_rate_limit_rps == 1
    assert settings.aggregate_price_deadline_ms == 750
    assert settings.aggregate_quote_deadline_ms == 950


def test_providers_enabled_can_be_overridden_directly() -> None:
    settings = Settings(providers_enabled=["curve"])
    assert settings.providers_enabled == ["curve"]


def test_api_key_rate_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="API_KEY_RATE_LIMIT_RPM must be > 0"):
        Settings(api_key_rate_limit_rpm=0)


def test_api_key_unauth_rate_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="API_KEY_UNAUTH_RATE_LIMIT_RPS must be > 0"):
        Settings(api_key_unauth_rate_limit_rps=0)

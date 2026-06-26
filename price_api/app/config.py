from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import (
    AliasChoices,
    AliasPath,
    Field,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

MIN_REQUEST_TIMEOUT_MS = 200
MAX_REQUEST_TIMEOUT_MS = 10000
ODOS_PUBLIC_BASE_URL = "https://api.odos.xyz"
ODOS_ENTERPRISE_BASE_URL = "https://enterprise-api.odos.xyz"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_format: str = "json"
    log_request_body: bool = False
    metrics_enabled: bool = True
    enable_readiness_strict: bool = False

    chain_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [1],
        validation_alias=AliasChoices("chain_ids", AliasPath("chains", "ids")),
    )
    rpc_urls: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("rpc_urls", AliasPath("rpc", "urls")),
    )

    provider_request_timeout_ms: int = Field(
        default=800,
        validation_alias=AliasChoices(
            "provider_request_timeout_ms",
            AliasPath("timeouts", "provider_request_timeout_ms"),
        ),
    )
    provider_max_retries: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "provider_max_retries",
            AliasPath("timeouts", "provider_max_retries"),
        ),
    )
    provider_http_trust_env: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "provider_http_trust_env",
            AliasPath("timeouts", "provider_http_trust_env"),
        ),
    )
    provider_pool_timeout_ms: int = Field(
        default=25,
        validation_alias=AliasChoices(
            "provider_pool_timeout_ms",
            AliasPath("transport", "provider_pool_timeout_ms"),
        ),
    )
    provider_connect_timeout_ms: int = Field(
        default=500,
        validation_alias=AliasChoices(
            "provider_connect_timeout_ms",
            AliasPath("transport", "provider_connect_timeout_ms"),
        ),
    )
    provider_read_timeout_ms: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "provider_read_timeout_ms",
            AliasPath("transport", "provider_read_timeout_ms"),
        ),
    )
    provider_write_timeout_ms: int = Field(
        default=500,
        validation_alias=AliasChoices(
            "provider_write_timeout_ms",
            AliasPath("transport", "provider_write_timeout_ms"),
        ),
    )
    provider_max_connections_per_provider: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "provider_max_connections_per_provider",
            AliasPath("transport", "provider_max_connections_per_provider"),
        ),
    )
    provider_max_keepalive_connections_per_provider: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "provider_max_keepalive_connections_per_provider",
            AliasPath("transport", "provider_max_keepalive_connections_per_provider"),
        ),
    )
    provider_keepalive_expiry_s: float = Field(
        default=2.0,
        validation_alias=AliasChoices(
            "provider_keepalive_expiry_s",
            AliasPath("transport", "provider_keepalive_expiry_s"),
        ),
    )
    provider_client_ttl_s: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "provider_client_ttl_s",
            AliasPath("transport", "provider_client_ttl_s"),
        ),
    )
    provider_client_max_requests: int = Field(
        default=5000,
        validation_alias=AliasChoices(
            "provider_client_max_requests",
            AliasPath("transport", "provider_client_max_requests"),
        ),
    )
    provider_recycle_pool_timeout_threshold: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "provider_recycle_pool_timeout_threshold",
            AliasPath("transport", "provider_recycle_pool_timeout_threshold"),
        ),
    )
    provider_recycle_window_s: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "provider_recycle_window_s",
            AliasPath("transport", "provider_recycle_window_s"),
        ),
    )

    provider_fanout_per_request: int = Field(
        default=8,
        validation_alias=AliasChoices(
            "provider_fanout_per_request",
            AliasPath("concurrency", "provider_fanout_per_request"),
        ),
    )
    provider_global_limit: int = Field(
        default=200,
        validation_alias=AliasChoices(
            "provider_global_limit",
            AliasPath("concurrency", "provider_global_limit"),
        ),
    )
    provider_global_units: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "provider_global_units",
            AliasPath("concurrency", "provider_global_units"),
        ),
    )
    provider_per_provider_units: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "provider_per_provider_units",
            AliasPath("concurrency", "provider_per_provider_units"),
        ),
    )
    vault_global_units: int = Field(
        default=16,
        validation_alias=AliasChoices(
            "vault_global_units",
            AliasPath("concurrency", "vault_global_units"),
        ),
    )
    admission_acquire_timeout_ms: int = Field(
        default=25,
        validation_alias=AliasChoices(
            "admission_acquire_timeout_ms",
            AliasPath("concurrency", "admission_acquire_timeout_ms"),
        ),
    )
    web3_limit: int = Field(
        default=32,
        validation_alias=AliasChoices(
            "web3_limit",
            AliasPath("concurrency", "web3_limit"),
        ),
    )
    provider_circuit_failure_window_s: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "provider_circuit_failure_window_s",
            AliasPath("circuit_breakers", "failure_window_s"),
        ),
    )
    provider_circuit_failure_threshold: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "provider_circuit_failure_threshold",
            AliasPath("circuit_breakers", "failure_threshold"),
        ),
    )
    provider_circuit_open_duration_s: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "provider_circuit_open_duration_s",
            AliasPath("circuit_breakers", "open_duration_s"),
        ),
    )
    provider_circuit_half_open_probe_count: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "provider_circuit_half_open_probe_count",
            AliasPath("circuit_breakers", "half_open_probe_count"),
        ),
    )
    vault_positive_cache_ttl_s: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "vault_positive_cache_ttl_s",
            AliasPath("vault", "positive_cache_ttl_s"),
        ),
    )
    vault_negative_cache_ttl_s: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "vault_negative_cache_ttl_s",
            AliasPath("vault", "negative_cache_ttl_s"),
        ),
    )
    close_wait_ready_threshold: int = Field(
        default=80,
        validation_alias=AliasChoices(
            "close_wait_ready_threshold",
            AliasPath("readiness", "close_wait_ready_threshold"),
        ),
    )
    token_metadata_db_path: str = Field(
        default="data/token_metadata.sqlite3",
        validation_alias=AliasChoices(
            "token_metadata_db_path",
            AliasPath("token_metadata", "db_path"),
        ),
    )
    api_key_auth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "api_key_auth_enabled",
            AliasPath("security", "api_key_auth_enabled"),
        ),
    )
    api_key_db_path: str = Field(
        default="data/api_keys.sqlite3",
        validation_alias=AliasChoices(
            "api_key_db_path",
            AliasPath("security", "api_key_db_path"),
        ),
    )
    api_key_rate_limit_rpm: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "api_key_rate_limit_rpm",
            AliasPath("security", "api_key_rate_limit_rpm"),
        ),
    )
    api_key_unauth_access_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "api_key_unauth_access_enabled",
            AliasPath("security", "api_key_unauth_access_enabled"),
        ),
    )
    api_key_unauth_min_interval_seconds: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "api_key_unauth_min_interval_seconds",
            AliasPath("security", "api_key_unauth_min_interval_seconds"),
            "api_key_unauth_rate_limit_rps",
            AliasPath("security", "api_key_unauth_rate_limit_rps"),
        ),
    )

    providers_enabled: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["defillama", "curve", "odos", "lifi", "enso"],
        validation_alias=AliasChoices("providers_enabled", AliasPath("providers", "enabled")),
    )

    price_provider_priority: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "price_provider_priority",
            AliasPath("providers", "price_priority"),
        ),
    )
    quote_provider_priority: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "quote_provider_priority",
            AliasPath("providers", "quote_priority"),
        ),
    )

    lifi_api_key: str | None = None
    lifi_deny_exchanges: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "lifi_deny_exchanges",
            AliasPath("providers", "lifi", "deny_exchanges"),
        ),
    )
    odos_api_key: str | None = None
    odos_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("odos_base_url", AliasPath("providers", "odos", "base_url")),
    )
    enso_api_key: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=Path("config/app.toml")),
            file_secret_settings,
        )

    @field_validator("chain_ids", mode="before")
    @classmethod
    def _parse_chain_ids(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                loaded = json.loads(stripped)
                if isinstance(loaded, list):
                    return [int(item) for item in loaded]
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("rpc_urls", mode="before")
    @classmethod
    def _parse_rpc_urls(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                loaded = json.loads(stripped)
                if isinstance(loaded, list):
                    return [str(item).strip() for item in loaded if str(item).strip()]
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("providers_enabled", mode="before")
    @classmethod
    def _parse_providers_enabled(cls, value: object) -> object:
        parsed = _parse_string_list(value)
        if parsed is None:
            return value

        normalized: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            provider_id = item.strip().lower()
            if not provider_id or provider_id in seen:
                continue
            normalized.append(provider_id)
            seen.add(provider_id)
        return normalized

    @field_validator("log_format", mode="before")
    @classmethod
    def _normalize_log_format(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in {"json", "text"}:
            raise ValueError("LOG_FORMAT must be 'json' or 'text'")
        return normalized

    @field_validator("price_provider_priority", "quote_provider_priority", mode="before")
    @classmethod
    def _parse_provider_priority(cls, value: object) -> object:
        parsed = _parse_string_list(value)
        if parsed is None:
            return value

        normalized: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            provider_id = item.strip().lower()
            if not provider_id or provider_id in seen:
                continue
            normalized.append(provider_id)
            seen.add(provider_id)
        return normalized

    @field_validator("lifi_deny_exchanges", mode="before")
    @classmethod
    def _parse_lifi_deny_exchanges(cls, value: object) -> object:
        parsed = _parse_string_list(value)
        if parsed is None:
            return value

        normalized: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            exchange_id = item.strip().lower()
            if not exchange_id or exchange_id in seen:
                continue
            normalized.append(exchange_id)
            seen.add(exchange_id)
        return normalized

    @field_validator("odos_api_key", "odos_base_url", mode="before")
    @classmethod
    def _normalize_optional_secret_or_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _finalize_provider_settings(self) -> Settings:
        _require_positive(
            (
                ("PROVIDER_REQUEST_TIMEOUT_MS", self.provider_request_timeout_ms),
                ("PROVIDER_POOL_TIMEOUT_MS", self.provider_pool_timeout_ms),
                ("PROVIDER_CONNECT_TIMEOUT_MS", self.provider_connect_timeout_ms),
                ("PROVIDER_WRITE_TIMEOUT_MS", self.provider_write_timeout_ms),
                ("PROVIDER_CLIENT_TTL_S", self.provider_client_ttl_s),
                ("PROVIDER_CLIENT_MAX_REQUESTS", self.provider_client_max_requests),
                (
                    "PROVIDER_RECYCLE_POOL_TIMEOUT_THRESHOLD",
                    self.provider_recycle_pool_timeout_threshold,
                ),
                ("PROVIDER_RECYCLE_WINDOW_S", self.provider_recycle_window_s),
                ("ADMISSION_ACQUIRE_TIMEOUT_MS", self.admission_acquire_timeout_ms),
                (
                    "PROVIDER_CIRCUIT_FAILURE_WINDOW_S",
                    self.provider_circuit_failure_window_s,
                ),
                (
                    "PROVIDER_CIRCUIT_FAILURE_THRESHOLD",
                    self.provider_circuit_failure_threshold,
                ),
                (
                    "PROVIDER_CIRCUIT_OPEN_DURATION_S",
                    self.provider_circuit_open_duration_s,
                ),
                (
                    "PROVIDER_CIRCUIT_HALF_OPEN_PROBE_COUNT",
                    self.provider_circuit_half_open_probe_count,
                ),
                ("VAULT_POSITIVE_CACHE_TTL_S", self.vault_positive_cache_ttl_s),
                ("VAULT_NEGATIVE_CACHE_TTL_S", self.vault_negative_cache_ttl_s),
                ("PROVIDER_FANOUT_PER_REQUEST", self.provider_fanout_per_request),
                ("PROVIDER_GLOBAL_LIMIT", self.provider_global_limit),
                ("PROVIDER_PER_PROVIDER_UNITS", self.provider_per_provider_units),
                ("VAULT_GLOBAL_UNITS", self.vault_global_units),
                ("WEB3_LIMIT", self.web3_limit),
                ("API_KEY_RATE_LIMIT_RPM", self.api_key_rate_limit_rpm),
                (
                    "API_KEY_UNAUTH_MIN_INTERVAL_SECONDS",
                    self.api_key_unauth_min_interval_seconds,
                ),
            )
        )
        _require_optional_positive(
            (
                ("PROVIDER_READ_TIMEOUT_MS", self.provider_read_timeout_ms),
                (
                    "PROVIDER_MAX_CONNECTIONS_PER_PROVIDER",
                    self.provider_max_connections_per_provider,
                ),
                ("PROVIDER_GLOBAL_UNITS", self.provider_global_units),
            )
        )
        _require_non_negative(
            (
                ("PROVIDER_MAX_RETRIES", self.provider_max_retries),
                (
                    "PROVIDER_MAX_KEEPALIVE_CONNECTIONS_PER_PROVIDER",
                    self.provider_max_keepalive_connections_per_provider,
                ),
                ("PROVIDER_KEEPALIVE_EXPIRY_S", self.provider_keepalive_expiry_s),
                ("CLOSE_WAIT_READY_THRESHOLD", self.close_wait_ready_threshold),
            )
        )

        return self

    @property
    def aggregate_price_deadline_ms(self) -> int:
        return self.provider_request_timeout_ms + 100

    @property
    def aggregate_quote_deadline_ms(self) -> int:
        return self.provider_request_timeout_ms + 300

    @property
    def effective_provider_read_timeout_ms(self) -> int:
        return self.provider_read_timeout_ms or self.provider_request_timeout_ms

    @property
    def effective_provider_max_connections_per_provider(self) -> int:
        return self.provider_max_connections_per_provider or self.provider_global_limit

    @property
    def effective_provider_global_units(self) -> int:
        return self.provider_global_units or self.provider_global_limit

    @property
    def effective_odos_base_url(self) -> str:
        if self.odos_base_url:
            return self.odos_base_url.rstrip("/")
        if self.odos_api_key:
            return ODOS_ENTERPRISE_BASE_URL
        return ODOS_PUBLIC_BASE_URL


def _parse_string_list(value: object) -> list[str] | None:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            loaded = json.loads(stripped)
            if isinstance(loaded, list):
                return [str(item) for item in loaded]
            return []
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _require_positive(fields: tuple[tuple[str, int | float], ...]) -> None:
    for field_name, value in fields:
        if value <= 0:
            raise ValueError(f"{field_name} must be > 0")


def _require_optional_positive(fields: tuple[tuple[str, int | None], ...]) -> None:
    for field_name, value in fields:
        if value is not None and value <= 0:
            raise ValueError(f"{field_name} must be > 0")


def _require_non_negative(fields: tuple[tuple[str, int | float], ...]) -> None:
    for field_name, value in fields:
        if value < 0:
            raise ValueError(f"{field_name} must be >= 0")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

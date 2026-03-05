from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, AliasPath, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


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
    web3_limit: int = Field(
        default=32,
        validation_alias=AliasChoices(
            "web3_limit",
            AliasPath("concurrency", "web3_limit"),
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
        default_factory=lambda: ["defillama", "curve", "lifi", "enso"],
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

    @model_validator(mode="after")
    def _finalize_provider_settings(self) -> Settings:
        if self.provider_request_timeout_ms <= 0:
            raise ValueError("PROVIDER_REQUEST_TIMEOUT_MS must be > 0")
        if self.provider_max_retries < 0:
            raise ValueError("PROVIDER_MAX_RETRIES must be >= 0")
        if self.provider_fanout_per_request <= 0:
            raise ValueError("PROVIDER_FANOUT_PER_REQUEST must be > 0")
        if self.provider_global_limit <= 0:
            raise ValueError("PROVIDER_GLOBAL_LIMIT must be > 0")
        if self.web3_limit <= 0:
            raise ValueError("WEB3_LIMIT must be > 0")
        if self.api_key_rate_limit_rpm <= 0:
            raise ValueError("API_KEY_RATE_LIMIT_RPM must be > 0")
        if self.api_key_unauth_min_interval_seconds <= 0:
            raise ValueError("API_KEY_UNAUTH_MIN_INTERVAL_SECONDS must be > 0")

        return self

    @property
    def aggregate_price_deadline_ms(self) -> int:
        return self.provider_request_timeout_ms + 100

    @property
    def aggregate_quote_deadline_ms(self) -> int:
        return self.provider_request_timeout_ms + 300


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

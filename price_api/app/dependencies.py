from __future__ import annotations

from functools import lru_cache

from price_api.app.config import Settings, get_settings
from price_api.core.aggregator import AggregatorService
from price_api.providers.registry import ProviderRegistry
from price_api.security.anon_limiter import AnonymousRateLimiter
from price_api.security.store import ApiKeyStore
from price_api.token_metadata.resolver import TokenMetadataResolver
from price_api.vault.resolver import VaultResolver


@lru_cache(maxsize=1)
def get_provider_registry() -> ProviderRegistry:
    settings = get_settings()
    return ProviderRegistry(settings)


@lru_cache(maxsize=1)
def get_vault_resolver() -> VaultResolver:
    settings = get_settings()
    return VaultResolver(settings)


@lru_cache(maxsize=1)
def get_aggregator_service() -> AggregatorService:
    settings: Settings = get_settings()
    registry = get_provider_registry()
    vault_resolver = get_vault_resolver()
    return AggregatorService(settings=settings, registry=registry, vault_resolver=vault_resolver)


@lru_cache(maxsize=1)
def get_token_metadata_resolver() -> TokenMetadataResolver:
    settings = get_settings()
    return TokenMetadataResolver(settings)


@lru_cache(maxsize=1)
def get_api_key_store() -> ApiKeyStore:
    settings = get_settings()
    return ApiKeyStore(db_path=settings.api_key_db_path)


@lru_cache(maxsize=1)
def get_anonymous_rate_limiter() -> AnonymousRateLimiter:
    return AnonymousRateLimiter()

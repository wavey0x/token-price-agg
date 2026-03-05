from __future__ import annotations

from functools import lru_cache

from token_price_agg.app.config import Settings, get_settings
from token_price_agg.core.aggregator import AggregatorService
from token_price_agg.providers.registry import ProviderRegistry
from token_price_agg.security.anon_limiter import AnonymousRateLimiter
from token_price_agg.security.store import ApiKeyStore
from token_price_agg.token_metadata.resolver import TokenMetadataResolver
from token_price_agg.vault.resolver import VaultResolver


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

from __future__ import annotations

import logging
from enum import Enum

from price_api.app.config import Settings
from price_api.core.errors import InvalidRequestError
from price_api.core.models import ProviderCapability
from price_api.observability.metrics import set_provider_available
from price_api.providers.base import ProviderPlugin
from price_api.providers.clients.http import HttpClient
from price_api.providers.curve import CurveProvider
from price_api.providers.defillama import DefiLlamaProvider
from price_api.providers.enso import EnsoProvider
from price_api.providers.lifi import LiFiProvider
from price_api.providers.odos import OdosProvider

_LOGGER = logging.getLogger("price_api.registry")


class Operation(str, Enum):
    PRICE = "price"
    QUOTE = "quote"


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http_clients: dict[str, HttpClient] = {}
        self._plugins = self._build_plugins()
        self._warn_invalid_priority_entries()
        self._sync_provider_availability_metrics()

    def _new_http_client(self, *, provider_id: str) -> HttpClient:
        client = HttpClient(
            timeout_ms=self._settings.provider_request_timeout_ms,
            max_retries=self._settings.provider_max_retries,
            trust_env=self._settings.provider_http_trust_env,
            max_connections=self._settings.effective_provider_max_connections_per_provider,
            max_keepalive_connections=(
                self._settings.provider_max_keepalive_connections_per_provider
            ),
            keepalive_expiry_s=self._settings.provider_keepalive_expiry_s,
            pool_timeout_ms=self._settings.provider_pool_timeout_ms,
            connect_timeout_ms=self._settings.provider_connect_timeout_ms,
            read_timeout_ms=self._settings.effective_provider_read_timeout_ms,
            write_timeout_ms=self._settings.provider_write_timeout_ms,
            client_ttl_s=self._settings.provider_client_ttl_s,
            client_max_requests=self._settings.provider_client_max_requests,
            recycle_pool_timeout_threshold=(self._settings.provider_recycle_pool_timeout_threshold),
            recycle_window_s=self._settings.provider_recycle_window_s,
            provider_id=provider_id,
        )
        self._http_clients[provider_id] = client
        return client

    def _build_plugins(self) -> dict[str, ProviderPlugin]:
        plugins: dict[str, ProviderPlugin] = {}
        enabled = set(self._settings.providers_enabled or [])

        if DefiLlamaProvider.id in enabled:
            plugins[DefiLlamaProvider.id] = DefiLlamaProvider(
                client=self._new_http_client(provider_id=DefiLlamaProvider.id)
            )

        if CurveProvider.id in enabled:
            plugins[CurveProvider.id] = CurveProvider(
                client=self._new_http_client(provider_id=CurveProvider.id)
            )

        if OdosProvider.id in enabled:
            plugins[OdosProvider.id] = OdosProvider(
                client=self._new_http_client(provider_id=OdosProvider.id),
                api_key=self._settings.odos_api_key,
                base_url=self._settings.effective_odos_base_url,
            )

        if LiFiProvider.id in enabled:
            lifi_available = bool(self._settings.lifi_api_key)
            lifi_reason = None if lifi_available else "missing_api_key"
            plugins[LiFiProvider.id] = LiFiProvider(
                client=self._new_http_client(provider_id=LiFiProvider.id),
                api_key=self._settings.lifi_api_key,
                deny_exchanges=self._settings.lifi_deny_exchanges,
                available=lifi_available,
                unavailable_reason=lifi_reason,
            )

        if EnsoProvider.id in enabled:
            enso_available = bool(self._settings.enso_api_key)
            enso_reason = None if enso_available else "missing_api_key"
            plugins[EnsoProvider.id] = EnsoProvider(
                client=self._new_http_client(provider_id=EnsoProvider.id),
                api_key=self._settings.enso_api_key,
                available=enso_available,
                unavailable_reason=enso_reason,
            )

        unknown = sorted(enabled - set(_known_provider_ids()))
        for provider_id in unknown:
            _LOGGER.warning(
                "unknown_enabled_provider_id",
                extra={
                    "provider": provider_id,
                },
            )

        return plugins

    def capabilities(self) -> list[ProviderCapability]:
        return [self._plugins[provider_id].capability() for provider_id in sorted(self._plugins)]

    def available_provider_count(self, *, chain_id: int | None = None) -> int:
        count = 0
        for plugin in self._plugins.values():
            if not plugin.available:
                continue
            if chain_id is not None and chain_id not in plugin.supported_chains:
                continue
            count += 1
        return count

    def resolve(
        self,
        *,
        provider_ids: list[str] | None,
        operation: Operation,
        chain_id: int,
    ) -> list[ProviderPlugin]:
        if provider_ids is None:
            selected = [
                plugin
                for plugin in self._plugins.values()
                if plugin.available
                and chain_id in plugin.supported_chains
                and self._supports(plugin, operation)
            ]
            return sorted(selected, key=lambda plugin: plugin.id)

        selected_plugins: list[ProviderPlugin] = []
        for provider_id in provider_ids:
            plugin = self._plugins.get(provider_id)
            if plugin is None:
                raise InvalidRequestError("UNKNOWN_PROVIDER", f"Unknown provider: {provider_id}")
            if chain_id not in plugin.supported_chains:
                raise InvalidRequestError(
                    "UNSUPPORTED_CHAIN", f"Provider {provider_id} does not support chain {chain_id}"
                )
            selected_plugins.append(plugin)

        return selected_plugins

    @staticmethod
    def _supports(plugin: ProviderPlugin, operation: Operation) -> bool:
        if operation == Operation.PRICE:
            return plugin.supports_price
        return plugin.supports_quote

    async def aclose(self) -> None:
        for client in self._http_clients.values():
            await client.close()

    def available_operation_count(
        self,
        *,
        operation: Operation,
        chain_id: int | None = None,
    ) -> int:
        return len(self.available_provider_ids(operation=operation, chain_id=chain_id))

    def available_provider_ids(
        self,
        *,
        operation: Operation,
        chain_id: int | None = None,
    ) -> list[str]:
        provider_ids: list[str] = []
        for plugin in self._plugins.values():
            if not plugin.available:
                continue
            if chain_id is not None and chain_id not in plugin.supported_chains:
                continue
            if not self._supports(plugin, operation):
                continue
            provider_ids.append(plugin.id)
        return provider_ids

    def transport_unhealthy(self) -> bool:
        return any(
            client.recent_pool_timeout_count() > 0 or client.recently_recycled_due_to_pool_timeout()
            for client in self._http_clients.values()
        )

    async def recycle_transports(self, *, reason: str) -> None:
        for client in self._http_clients.values():
            if client.recently_recycled(reason=reason):
                continue
            await client.recycle(reason=reason)

    def _sync_provider_availability_metrics(self) -> None:
        for provider_id, plugin in self._plugins.items():
            set_provider_available(provider=provider_id, available=plugin.available)

    def _warn_invalid_priority_entries(self) -> None:
        active = set(self._plugins)
        for operation, priority in (
            (Operation.PRICE.value, self._settings.price_provider_priority),
            (Operation.QUOTE.value, self._settings.quote_provider_priority),
        ):
            for provider_id in priority:
                if provider_id in active:
                    continue
                _LOGGER.warning(
                    "ignored_priority_provider",
                    extra={
                        "operation": operation,
                        "provider": provider_id,
                    },
                )


def _known_provider_ids() -> list[str]:
    return [
        DefiLlamaProvider.id,
        CurveProvider.id,
        OdosProvider.id,
        LiFiProvider.id,
        EnsoProvider.id,
    ]

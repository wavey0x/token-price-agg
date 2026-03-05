from __future__ import annotations

import logging
from enum import Enum

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import InvalidRequestError
from token_price_agg.core.models import ProviderCapability
from token_price_agg.observability.metrics import set_provider_available
from token_price_agg.providers.base import ProviderPlugin
from token_price_agg.providers.clients.http import HttpClient
from token_price_agg.providers.curve import CurveProvider
from token_price_agg.providers.defillama import DefiLlamaProvider
from token_price_agg.providers.enso import EnsoProvider
from token_price_agg.providers.lifi import LiFiProvider
from token_price_agg.providers.odos import OdosProvider

_LOGGER = logging.getLogger("token_price_agg.registry")


class Operation(str, Enum):
    PRICE = "price"
    QUOTE = "quote"


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http_client = HttpClient(
            timeout_ms=settings.provider_request_timeout_ms,
            max_retries=settings.provider_max_retries,
        )
        self._plugins = self._build_plugins()
        self._warn_invalid_priority_entries()
        self._sync_provider_availability_metrics()

    def _build_plugins(self) -> dict[str, ProviderPlugin]:
        plugins: dict[str, ProviderPlugin] = {}
        enabled = set(self._settings.providers_enabled or [])

        if DefiLlamaProvider.id in enabled:
            plugins[DefiLlamaProvider.id] = DefiLlamaProvider(client=self._http_client)

        if CurveProvider.id in enabled:
            plugins[CurveProvider.id] = CurveProvider(client=self._http_client)

        if OdosProvider.id in enabled:
            plugins[OdosProvider.id] = OdosProvider(client=self._http_client)

        if LiFiProvider.id in enabled:
            lifi_available = bool(self._settings.lifi_api_key)
            lifi_reason = None if lifi_available else "missing_api_key"
            plugins[LiFiProvider.id] = LiFiProvider(
                client=self._http_client,
                api_key=self._settings.lifi_api_key,
                available=lifi_available,
                unavailable_reason=lifi_reason,
            )

        if EnsoProvider.id in enabled:
            enso_available = bool(self._settings.enso_api_key)
            enso_reason = None if enso_available else "missing_api_key"
            plugins[EnsoProvider.id] = EnsoProvider(
                client=self._http_client,
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
        await self._http_client.close()

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

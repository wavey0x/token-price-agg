from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from price_api.api.schemas.responses import ReadyResponse
from price_api.app.config import Settings, get_settings
from price_api.app.dependencies import get_aggregator_service, get_provider_registry
from price_api.core.aggregator import AggregatorService
from price_api.observability.metrics import set_process_close_wait_sockets
from price_api.observability.system import count_process_close_wait_sockets
from price_api.providers.registry import Operation, ProviderRegistry

router = APIRouter(tags=["health"])


@router.get("/v1/ready", response_model=ReadyResponse)
async def ready(
    registry: ProviderRegistry = Depends(get_provider_registry),
    aggregator: AggregatorService = Depends(get_aggregator_service),
    settings: Settings = Depends(get_settings),
) -> ReadyResponse | JSONResponse:
    available_count = registry.available_provider_count(chain_id=1)
    price_providers = set(registry.available_provider_ids(operation=Operation.PRICE, chain_id=1))
    quote_providers = set(registry.available_provider_ids(operation=Operation.QUOTE, chain_id=1))
    circuit_open = aggregator.circuit_open_providers()
    ready_price_providers = sorted(price_providers - circuit_open)
    ready_quote_providers = sorted(quote_providers - circuit_open)
    close_wait_count = count_process_close_wait_sockets()
    if close_wait_count is not None:
        set_process_close_wait_sockets(close_wait_count)
    close_wait_threshold_exceeded = (
        close_wait_count is not None and close_wait_count > settings.close_wait_ready_threshold
    )
    if close_wait_threshold_exceeded:
        await registry.recycle_transports(reason="close_wait")
    provider_transport_unhealthy = registry.transport_unhealthy()

    checks: dict[str, bool | int | str] = {
        "provider_registry": True,
        "available_providers": available_count,
        "ready_price_providers": len(ready_price_providers),
        "ready_quote_providers": len(ready_quote_providers),
        "circuit_open_providers": len(circuit_open),
        "provider_transport_unhealthy": provider_transport_unhealthy,
        "strict_mode": settings.enable_readiness_strict,
        "metrics_enabled": settings.metrics_enabled,
    }
    if close_wait_count is not None:
        checks["close_wait_sockets"] = close_wait_count

    is_ready = True
    if settings.enable_readiness_strict and available_count == 0:
        is_ready = False
        checks["reason"] = "no_available_providers"
    elif price_providers and not ready_price_providers:
        is_ready = False
        checks["reason"] = "no_ready_price_providers"
    elif quote_providers and not ready_quote_providers:
        is_ready = False
        checks["reason"] = "no_ready_quote_providers"
    elif provider_transport_unhealthy:
        is_ready = False
        checks["reason"] = "provider_transport_unhealthy"
    elif close_wait_threshold_exceeded:
        is_ready = False
        checks["reason"] = "close_wait_threshold_exceeded"

    payload = ReadyResponse(status="ok" if is_ready else "not_ready", checks=checks)
    if is_ready:
        return payload
    return JSONResponse(status_code=503, content=payload.model_dump())

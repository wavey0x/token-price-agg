from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from token_price_agg.api.schemas.responses import ReadyResponse
from token_price_agg.app.config import Settings, get_settings
from token_price_agg.app.dependencies import get_provider_registry
from token_price_agg.providers.registry import ProviderRegistry

router = APIRouter(tags=["health"])


@router.get("/v1/ready", response_model=ReadyResponse)
async def ready(
    registry: ProviderRegistry = Depends(get_provider_registry),
    settings: Settings = Depends(get_settings),
) -> ReadyResponse | JSONResponse:
    available_count = registry.available_provider_count(chain_id=1)

    checks: dict[str, bool | int | str] = {
        "provider_registry": True,
        "available_providers": available_count,
        "strict_mode": settings.enable_readiness_strict,
        "metrics_enabled": settings.metrics_enabled,
    }

    is_ready = True
    if settings.enable_readiness_strict and available_count == 0:
        is_ready = False
        checks["reason"] = "no_available_providers"

    payload = ReadyResponse(status="ok" if is_ready else "not_ready", checks=checks)
    if is_ready:
        return payload
    return JSONResponse(status_code=503, content=payload.model_dump())

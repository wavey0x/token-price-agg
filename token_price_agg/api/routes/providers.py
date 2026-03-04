from __future__ import annotations

from fastapi import APIRouter, Depends

from token_price_agg.api.schemas.responses import ProvidersResponse
from token_price_agg.app.dependencies import get_provider_registry
from token_price_agg.providers.registry import ProviderRegistry

router = APIRouter(tags=["providers"])


@router.get("/v1/providers", response_model=ProvidersResponse)
async def providers(
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> ProvidersResponse:
    return ProvidersResponse(providers=registry.capabilities())

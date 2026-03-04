from __future__ import annotations

from fastapi import APIRouter

from token_price_agg.api.schemas.responses import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")

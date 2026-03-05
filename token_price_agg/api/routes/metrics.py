from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from token_price_agg.app.config import Settings, get_settings

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics(settings: Settings = Depends(get_settings)) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled")

    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import ClassVar

import pytest

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import PriceResult, ProviderPriceRequest, TokenRef
from token_price_agg.core.provider_runner import ProviderOperationRunner
from token_price_agg.providers.base import ProviderPlugin

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.mark.asyncio
async def test_provider_runner_cancels_child_tasks_when_parent_is_cancelled() -> None:
    started = asyncio.Event()
    events: list[str] = []

    class SlowPlugin(ProviderPlugin):
        id: ClassVar[str] = "slow"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            events.append("started")
            started.set()
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                events.append("cancelled")
                raise
            events.append("completed")
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=1000,
            )

    runner = ProviderOperationRunner(
        settings=Settings(provider_fanout_per_request=1, provider_global_limit=1)
    )
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))
    task = asyncio.create_task(runner.run_prices(plugins=[SlowPlugin()], req=req, deadline_ms=5000))

    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["started", "cancelled"]

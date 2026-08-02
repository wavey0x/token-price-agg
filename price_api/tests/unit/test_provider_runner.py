from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import ClassVar

import pytest

from price_api.app.config import Settings
from price_api.core.errors import AdmissionRejectedError, ErrorInfo, ErrorType, ProviderStatus
from price_api.core.models import PriceResult, ProviderPriceRequest, TokenRef
from price_api.core.provider_runner import ProviderOperationRunner
from price_api.providers.base import ProviderPlugin

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"


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


@pytest.mark.asyncio
async def test_provider_runner_releases_partial_admission_when_cancelled() -> None:
    blocker_started = asyncio.Event()
    release_blocker = asyncio.Event()

    class ProviderA(ProviderPlugin):
        id: ClassVar[str] = "provider-a"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=1,
            )

    class ProviderB(ProviderPlugin):
        id: ClassVar[str] = "provider-b"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            blocker_started.set()
            await release_blocker.wait()
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=1,
            )

    runner = ProviderOperationRunner(
        settings=Settings(
            provider_fanout_per_request=2,
            provider_global_units=10,
            provider_per_provider_units=1,
            admission_acquire_timeout_ms=5000,
        )
    )
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))
    blocker = asyncio.create_task(
        runner.run_prices(plugins=[ProviderB()], req=req, deadline_ms=5000)
    )
    await blocker_started.wait()

    cancelled = asyncio.create_task(
        runner.run_prices(plugins=[ProviderA(), ProviderB()], req=req, deadline_ms=5000)
    )
    for _ in range(100):
        if runner._capacity.provider_limiter("provider-a").used == 1:
            break
        await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    assert runner._capacity.provider_limiter("provider-a").used == 0
    assert runner._capacity.provider_limiter("provider-b").used == 1

    release_blocker.set()
    await blocker


@pytest.mark.asyncio
async def test_provider_runner_rejects_success_for_wrong_token_identity() -> None:
    class WrongTokenPlugin(ProviderPlugin):
        id: ClassVar[str] = "wrong-token"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=TokenRef(chain_id=req.chain_id, address=DAI),
                price_usd=Decimal("1"),
                latency_ms=1,
            )

    runner = ProviderOperationRunner(settings=Settings())
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))

    result = (await runner.run_prices(plugins=[WrongTokenPlugin()], req=req, deadline_ms=1000))[0]

    assert result.status == ProviderStatus.ERROR
    assert result.error is not None
    assert result.error.type == ErrorType.INTERNAL


@pytest.mark.asyncio
async def test_provider_runner_rejects_global_admission_when_capacity_is_full() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowPlugin(ProviderPlugin):
        id: ClassVar[str] = "slow"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            started.set()
            await release.wait()
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=1,
            )

    runner = ProviderOperationRunner(
        settings=Settings(
            provider_fanout_per_request=2,
            provider_global_units=1,
            provider_per_provider_units=2,
            admission_acquire_timeout_ms=1,
        )
    )
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))
    first = asyncio.create_task(
        runner.run_prices(
            plugins=[SlowPlugin()],
            req=req,
            deadline_ms=5000,
        )
    )
    await started.wait()

    with pytest.raises(AdmissionRejectedError) as exc_info:
        await runner.run_prices(
            plugins=[SlowPlugin()],
            req=req,
            deadline_ms=5000,
        )

    release.set()
    await first
    assert exc_info.value.type == "SERVICE_OVERLOADED"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_provider_runner_allows_concurrent_requests_when_capacity_is_available() -> None:
    both_started = asyncio.Event()
    release = asyncio.Event()
    started_count = 0

    class SlowPlugin(ProviderPlugin):
        id: ClassVar[str] = "slow"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            await release.wait()
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=1,
            )

    runner = ProviderOperationRunner(
        settings=Settings(
            provider_fanout_per_request=2,
            provider_global_units=10,
            provider_per_provider_units=2,
            admission_acquire_timeout_ms=1,
        )
    )
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))
    first = asyncio.create_task(
        runner.run_prices(
            plugins=[SlowPlugin()],
            req=req,
            deadline_ms=5000,
        )
    )
    second = asyncio.create_task(
        runner.run_prices(
            plugins=[SlowPlugin()],
            req=req,
            deadline_ms=5000,
        )
    )
    await both_started.wait()

    release.set()
    first_results, second_results = await asyncio.gather(first, second)
    assert first_results[0].status == ProviderStatus.OK
    assert second_results[0].status == ProviderStatus.OK


@pytest.mark.asyncio
async def test_provider_runner_returns_provider_failure_when_provider_lane_is_full() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowPlugin(ProviderPlugin):
        id: ClassVar[str] = "slow"
        supports_price: ClassVar[bool] = True

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            started.set()
            await release.wait()
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.OK,
                token=req.token,
                price_usd=Decimal("1"),
                latency_ms=1,
            )

    runner = ProviderOperationRunner(
        settings=Settings(
            provider_fanout_per_request=2,
            provider_global_units=10,
            provider_per_provider_units=1,
            admission_acquire_timeout_ms=1,
        )
    )
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))
    first = asyncio.create_task(
        runner.run_prices(
            plugins=[SlowPlugin()],
            req=req,
            deadline_ms=5000,
        )
    )
    await started.wait()

    results = await runner.run_prices(
        plugins=[SlowPlugin()],
        req=req,
        deadline_ms=5000,
    )

    release.set()
    await first
    assert results[0].status == ProviderStatus.ERROR
    assert results[0].error is not None
    assert results[0].error.type.value == "PROVIDER_UNAVAILABLE"
    assert results[0].error.message == "Provider capacity unavailable"


@pytest.mark.asyncio
async def test_provider_runner_opens_provider_circuit_after_retriable_failures() -> None:
    class FailingPlugin(ProviderPlugin):
        id: ClassVar[str] = "failing"
        supports_price: ClassVar[bool] = True

        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
            self.calls += 1
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.ERROR,
                token=req.token,
                latency_ms=1,
                error=ErrorInfo(
                    type=ErrorType.INTERNAL_TRANSPORT_TIMEOUT,
                    message="pool timeout",
                ),
            )

    plugin = FailingPlugin()
    runner = ProviderOperationRunner(
        settings=Settings(
            provider_fanout_per_request=1,
            provider_global_units=5,
            provider_per_provider_units=5,
            provider_circuit_failure_threshold=2,
            provider_circuit_failure_window_s=30,
            provider_circuit_open_duration_s=30,
            admission_acquire_timeout_ms=1,
        )
    )
    req = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))

    await runner.run_prices(plugins=[plugin], req=req, deadline_ms=1000)
    await runner.run_prices(plugins=[plugin], req=req, deadline_ms=1000)
    results = await runner.run_prices(plugins=[plugin], req=req, deadline_ms=1000)

    assert plugin.calls == 2
    assert results[0].status == ProviderStatus.ERROR
    assert results[0].error is not None
    assert results[0].error.type.value == "PROVIDER_UNAVAILABLE"

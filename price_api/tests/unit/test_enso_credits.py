from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx
from prometheus_client import REGISTRY

from price_api.app.config import Settings
from price_api.core.models import ProviderPriceRequest, ProviderQuoteRequest, TokenRef
from price_api.observability.metrics import ENSO_CREDITS_UPDATED
from price_api.providers.clients.http import HttpResponse
from price_api.providers.enso import EnsoProvider
from price_api.providers.registry import ProviderRegistry
from price_api.tests.e2e.helpers import token


@pytest.mark.asyncio
@pytest.mark.parametrize(("used", "status"), [(94999, 200), (95000, 200), (100001, 429)])
@pytest.mark.parametrize("operation", ["price", "quote"])
async def test_credit_headers_are_recorded_on_success_and_exhaustion(
    used: int,
    status: int,
    operation: str,
) -> None:
    registry = ProviderRegistry(Settings(enso_api_key="fixture", providers_enabled=["enso"]))
    provider = registry._plugins["enso"]
    assert isinstance(provider, EnsoProvider)
    sell = TokenRef(chain_id=1, address=token("CRV"))
    buy = TokenRef(chain_id=1, address=token("USDC"))
    with respx.mock as router:
        route = router.get(url__regex=r"https://api\.enso\.(finance|build)/.*").respond(
            status,
            json={"amountOut": "1000000", "price": "1"},
            headers={"X-Enso-Credits-Used": str(used), "X-Enso-Credits-Limit": "100000"},
        )
        if operation == "price":
            await provider.get_price(ProviderPriceRequest(chain_id=1, token=sell))
        else:
            await provider.get_quote(
                ProviderQuoteRequest(chain_id=1, token_in=sell, token_out=buy, amount_in=10**18)
            )
        assert route.calls.last.request.headers["authorization"] == "Bearer fixture"
    assert REGISTRY.get_sample_value("price_api_enso_credits_used") == used
    assert REGISTRY.get_sample_value("price_api_enso_credits_limit") == 100000
    assert REGISTRY.get_sample_value("price_api_enso_credits_remaining") == max(100000 - used, 0)
    assert (REGISTRY.get_sample_value("price_api_enso_credits_updated_timestamp_seconds") or 0) > 0
    await registry.aclose()


@pytest.mark.parametrize(
    ("used", "limit"), [("nan", "100"), ("1", "inf"), ("-1", "100"), ("1", "0"), ("?", "100")]
)
def test_invalid_credit_headers_do_not_mark_monitoring_fresh(used: str, limit: str) -> None:
    provider = EnsoProvider(client=AsyncMock(), api_key="fixture", available=True)
    ENSO_CREDITS_UPDATED.set(123)
    provider._record_credits(
        HttpResponse(
            status_code=200,
            json_data={},
            headers={"x-enso-credits-used": used, "x-enso-credits-limit": limit},
        )
    )
    provider._record_credits(HttpResponse(status_code=200, json_data={}, headers={}))
    assert REGISTRY.get_sample_value("price_api_enso_credits_updated_timestamp_seconds") == 123


@pytest.mark.asyncio
async def test_monitor_refreshes_on_startup_and_when_headers_are_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProviderRegistry(Settings(enso_api_key="fixture", providers_enabled=["enso"]))
    provider = registry._plugins["enso"]
    assert isinstance(provider, EnsoProvider)
    clock = [1000.0]
    monkeypatch.setattr(
        "price_api.providers.enso.time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            time=lambda: 1700000000,
        ),
    )
    sleeps = 0

    async def advance(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            clock[0] += 60  # Ordinary traffic supplied fresh headers; no extra request.
        elif sleeps == 2:
            clock[0] += 900
        else:
            raise asyncio.CancelledError

    monkeypatch.setattr("price_api.providers.enso.asyncio", SimpleNamespace(sleep=advance))
    with respx.mock as router:
        route = router.get("https://api.enso.build/api/v1/account/accountId").respond(
            200,
            json="fixture-account",
            headers={"X-Enso-Credits-Used": "100", "X-Enso-Credits-Limit": "100000"},
        )
        with pytest.raises(asyncio.CancelledError):
            await provider.monitor_credits()
        assert route.call_count == 2
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_cancels_credit_monitor_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ProviderRegistry(Settings(enso_api_key="fixture", providers_enabled=["enso"]))
    provider = registry._plugins["enso"]
    assert isinstance(provider, EnsoProvider)
    monkeypatch.setattr(provider, "monitor_credits", AsyncMock(side_effect=asyncio.Event().wait))
    registry.start()
    task = registry._credit_task
    assert task is not None
    registry.start()
    assert registry._credit_task is task
    await asyncio.sleep(0)
    await registry.aclose()
    assert task.cancelled()

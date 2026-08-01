from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import respx
from httpx import Response

from price_api.app.config import Settings
from price_api.core.errors import ErrorType, ProviderStatus
from price_api.core.models import ProviderPriceRequest, ProviderQuoteRequest, TokenRef
from price_api.core.provider_runner import ProviderOperationRunner
from price_api.providers.clients.http import HttpClient
from price_api.providers.curve import CurveProvider
from price_api.providers.defillama import DefiLlamaProvider
from price_api.providers.enso import EnsoProvider
from price_api.providers.http_helpers import json_transport_outcome, timed_get
from price_api.providers.lifi import LiFiProvider


async def _start_delayed_json_server(*, delay_s: float) -> tuple[asyncio.Server, str]:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(4096)
            await asyncio.sleep(delay_s)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"content-type: application/json\r\n"
                b"content-length: 2\r\n"
                b"connection: close\r\n\r\n{}"
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    assert server.sockets is not None
    socket = server.sockets[0]
    host, port = socket.getsockname()[:2]
    return server, f"http://{host}:{port}/"


@pytest.mark.asyncio
async def test_provider_http_pool_timeout_maps_to_internal_transport_timeout() -> None:
    server, url = await _start_delayed_json_server(delay_s=0.4)
    client = HttpClient(
        timeout_ms=1000,
        max_retries=0,
        max_connections=1,
        max_keepalive_connections=0,
    )
    first = asyncio.create_task(
        timed_get(
            client=client,
            url=url,
            timeout_ms=1000,
            provider_id="test",
            operation="price",
        )
    )

    try:
        await asyncio.sleep(0.05)
        second = await timed_get(
            client=client,
            url=url,
            timeout_ms=100,
            provider_id="test",
            operation="price",
        )
        transport = json_transport_outcome(call=second, provider_name="Test")
        first_result = await first
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert first_result.response is not None
    assert second.timeout is True
    assert second.timeout_error_type == ErrorType.INTERNAL_TRANSPORT_TIMEOUT
    assert second.transport_error_type == "PoolTimeout"
    assert transport.failure is not None
    assert transport.failure.error_type == ErrorType.INTERNAL_TRANSPORT_TIMEOUT
    assert "internal transport" in transport.failure.message


@pytest.mark.asyncio
async def test_provider_http_read_timeout_stays_provider_timeout() -> None:
    server, url = await _start_delayed_json_server(delay_s=0.3)
    client = HttpClient(timeout_ms=50, max_retries=0)

    try:
        call = await timed_get(
            client=client,
            url=url,
            provider_id="test",
            operation="price",
        )
        transport = json_transport_outcome(call=call, provider_name="Test")
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert call.timeout is True
    assert call.timeout_error_type == ErrorType.TIMEOUT
    assert call.transport_error_type == "ReadTimeout"
    assert transport.failure is not None
    assert transport.failure.error_type == ErrorType.TIMEOUT


@pytest.mark.asyncio
async def test_provider_http_client_recycles_after_repeated_pool_timeouts() -> None:
    client = HttpClient(
        timeout_ms=1,
        max_retries=0,
        connect_timeout_ms=1,
        read_timeout_ms=1,
        write_timeout_ms=1,
        recycle_pool_timeout_threshold=2,
        recycle_window_s=30,
        provider_id="test",
    )

    try:
        await client.record_pool_timeout()
        assert client.recently_recycled_due_to_pool_timeout() is False

        await client.record_pool_timeout()

        assert client.recently_recycled_due_to_pool_timeout() is True
        assert client.recent_pool_timeout_count() == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_defillama_price_success() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = DefiLlamaProvider(client=client)

    req = ProviderPriceRequest(
        chain_id=1, token=TokenRef(chain_id=1, address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    )
    coin_key = f"ethereum:{req.token.address}"

    with respx.mock(assert_all_called=True) as router:
        router.get(f"https://coins.llama.fi/prices/current/{coin_key}").mock(
            return_value=Response(
                200,
                json={
                    "coins": {
                        coin_key: {
                            "price": "1.001",
                            "timestamp": 1700000000,
                            "symbol": "USDC",
                            "decimals": 6,
                        }
                    }
                },
            )
        )

        result = await provider.get_price(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.price_usd == Decimal("1.001")
    assert result.token is not None
    assert result.token.address == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    assert result.token.symbol == "USDC"
    assert result.token.decimals == 6


@pytest.mark.asyncio
async def test_curve_quote_success() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = CurveProvider(client=client)

    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),
        token_out=TokenRef(chain_id=1, address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
        amount_in=10**18,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(
                200,
                json=[{"amountOut": ["999000"], "priceImpact": 0, "route": [{"hop": 1}]}],
            )
        )

        result = await provider.get_quote(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.amount_out == 999000
    assert result.estimated_gas is None
    assert result.price_impact_bps == 0
    assert result.route == {"steps": [{"hop": 1}]}


@pytest.mark.asyncio
async def test_curve_quote_empty_list_maps_to_no_route() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = CurveProvider(client=client)

    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address="0xD533a949740bb3306d119CC777fa900bA034cd52"),
        token_out=TokenRef(chain_id=1, address="0xB5571E76693ba60110B5811DD650FFefce1C955f"),
        amount_in=3046763837527638654979,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get("https://www.curve.finance/api/router/v1/routes").mock(
            return_value=Response(200, json=[]),
        )

        result = await provider.get_quote(req)

    await client.close()

    assert result.status == ProviderStatus.NO_ROUTE
    assert result.amount_out is None
    assert result.error is not None
    assert result.error.type.value == "NO_ROUTE"
    assert result.error.code is None
    assert result.error.message == "No route found"


@pytest.mark.asyncio
async def test_lifi_unavailable_without_key() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = LiFiProvider(
        client=client,
        api_key=None,
        available=False,
        unavailable_reason="missing_api_key",
    )

    req = ProviderPriceRequest(
        chain_id=1, token=TokenRef(chain_id=1, address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    )
    runner = ProviderOperationRunner(
        settings=Settings(provider_fanout_per_request=2, provider_global_limit=2)
    )
    results = await runner.run_prices(plugins=[provider], req=req, deadline_ms=700)
    await client.close()

    assert len(results) == 1
    result = results[0]
    assert result.status == ProviderStatus.BAD_REQUEST
    assert result.error is not None
    assert result.error.type.value == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_enso_price_success_with_millisecond_timestamp() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = EnsoProvider(
        client=client,
        api_key="dummy",
        available=True,
    )
    req = ProviderPriceRequest(
        chain_id=1, token=TokenRef(chain_id=1, address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    )

    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://api.enso.finance/api/v1/prices/1/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        ).mock(
            return_value=Response(
                200,
                json={
                    "decimals": 6,
                    "symbol": "USDC",
                    "price": 0.9999,
                    "timestamp": 1_772_636_791_070,
                    "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    "chainId": 1,
                    "name": "USDC",
                },
            )
        )

        result = await provider.get_price(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.price_usd is not None
    assert result.as_of is not None
    assert result.as_of.year == 2026


@pytest.mark.asyncio
async def test_enso_quote_uses_valid_from_address() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = EnsoProvider(client=client, api_key="dummy", available=True)
    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        token_out=TokenRef(chain_id=1, address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
        amount_in=10**18,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://api.enso.build/api/v1/shortcuts/route",
            params={
                "chainId": "1",
                "fromAddress": "0x1111111111111111111111111111111111111111",
                "tokenIn": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "tokenOut": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                "amountIn": str(10**18),
                "slippage": "300",
            },
        ).mock(
            return_value=Response(
                200,
                json={
                    "amountOut": "2125000000",
                    "minAmountOut": "2061000000",
                    "gas": "1602414",
                    "priceImpact": 23,
                },
            )
        )

        result = await provider.get_quote(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.amount_out == 2125000000
    assert result.amount_out_min == 2061000000
    assert result.estimated_gas == 1602414
    assert result.price_impact_bps == 23


@pytest.mark.asyncio
async def test_enso_quote_converts_human_decimal_amounts_to_base_units() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = EnsoProvider(client=client, api_key="dummy", available=True)
    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        token_out=TokenRef(chain_id=1, address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
        amount_in=10**18,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.enso.build/api/v1/shortcuts/route").mock(
            return_value=Response(
                200,
                json={
                    "amountOut": "2125.893537",
                    "minAmountOut": "2119.515856",
                    "tokenOut": {"decimals": 6, "symbol": "USDC"},
                },
            )
        )
        result = await provider.get_quote(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.amount_out == 2125893537
    assert result.amount_out_min == 2119515856


@pytest.mark.asyncio
async def test_lifi_quote_converts_human_decimal_amounts_to_base_units() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = LiFiProvider(client=client, api_key="dummy", available=True)
    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        token_out=TokenRef(chain_id=1, address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
        amount_in=10**18,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get("https://li.quest/v1/quote").mock(
            return_value=Response(
                200,
                json={
                    "estimate": {
                        "toAmount": "2125.893537",
                        "toAmountMin": "2119.515856",
                        "toToken": {"decimals": 6, "symbol": "USDC"},
                    }
                },
            )
        )
        result = await provider.get_quote(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.amount_out == 2125893537
    assert result.amount_out_min == 2119515856


@pytest.mark.asyncio
async def test_lifi_quote_sends_configured_denied_exchanges() -> None:
    client = HttpClient(timeout_ms=500, max_retries=0)
    provider = LiFiProvider(
        client=client,
        api_key="dummy",
        deny_exchanges=["fly"],
        available=True,
    )
    req = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        token_out=TokenRef(chain_id=1, address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
        amount_in=10**18,
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://li.quest/v1/quote").mock(
            return_value=Response(
                200,
                json={"estimate": {"toAmount": "2125893537"}},
            )
        )

        result = await provider.get_quote(req)

    await client.close()

    request = route.calls[0].request
    assert request.url.params["denyExchanges"] == "fly"
    assert result.status == ProviderStatus.OK
    assert result.amount_out == 2125893537

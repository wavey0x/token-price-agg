from __future__ import annotations

from decimal import Decimal

import pytest
import respx
from httpx import Response

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import ProviderPriceRequest, ProviderQuoteRequest, TokenRef
from token_price_agg.core.provider_runner import ProviderOperationRunner
from token_price_agg.providers.clients.http import HttpClient
from token_price_agg.providers.curve import CurveProvider
from token_price_agg.providers.defillama import DefiLlamaProvider
from token_price_agg.providers.enso import EnsoProvider
from token_price_agg.providers.lifi import LiFiProvider


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
                json={
                    "data": {
                        "amountOut": "999000",
                        "estimatedGas": 345000,
                        "priceImpact": "0.0015",
                        "route": {"hops": 2},
                    }
                },
            )
        )

        result = await provider.get_quote(req)

    await client.close()

    assert result.status == ProviderStatus.OK
    assert result.amount_out == 999000
    assert result.estimated_gas == 345000
    assert result.price_impact_bps == 15


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
    assert result.status == ProviderStatus.INVALID_REQUEST
    assert result.error is not None
    assert result.error.code == "PROVIDER_UNAVAILABLE"


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

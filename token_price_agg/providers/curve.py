from __future__ import annotations

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from token_price_agg.core.validator import NATIVE_TOKEN_ALIAS
from token_price_agg.providers.base import ProviderPlugin
from token_price_agg.providers.clients.http import HttpClient
from token_price_agg.providers.http_helpers import json_transport_outcome, timed_get
from token_price_agg.providers.parsing import (
    decimal_to_bps,
    get_first,
    get_nested,
    parse_datetime,
    parse_decimal,
    parse_int,
)
from token_price_agg.providers.utils import error_from_status


class CurveProvider(ProviderPlugin):
    id = "curve"
    supports_price = True
    supports_quote = True

    def __init__(self, *, client: HttpClient, available: bool = True) -> None:
        super().__init__(available=available)
        self._client = client

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        call = await timed_get(
            client=self._client,
            url=f"https://prices.curve.finance/v1/usd_price/ethereum/{req.token.address}",
        )
        transport = json_transport_outcome(call=call, provider_name="Curve")
        if transport.failure is not None:
            return PriceResult(
                provider=self.id,
                status=transport.failure.status,
                token=req.token,
                latency_ms=transport.failure.latency_ms,
                error=error_from_status(transport.failure.status, transport.failure.message),
            )

        payload = transport.payload
        assert payload is not None
        latency_ms = transport.latency_ms

        price_value = get_first(payload, ["usdPrice", "usd_price", "price"])
        if price_value is None:
            data = payload.get("data")
            if isinstance(data, dict):
                price_value = get_first(data, ["usdPrice", "usd_price", "price"])

        price = parse_decimal(price_value)
        if price is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UNSUPPORTED_TOKEN,
                token=req.token,
                latency_ms=latency_ms,
                error=error_from_status(ProviderStatus.UNSUPPORTED_TOKEN, "Token not supported"),
            )

        timestamp_val = get_first(payload, ["timestamp", "asOf"])
        as_of = parse_datetime(timestamp_val)

        return PriceResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token=req.token,
            price_usd=price,
            latency_ms=latency_ms,
            as_of=as_of,
        )

    async def get_quote(self, req: ProviderQuoteRequest) -> QuoteResult:
        token_in = _to_curve_native_alias(req.token_in.address)
        token_out = _to_curve_native_alias(req.token_out.address)

        call = await timed_get(
            client=self._client,
            url="https://www.curve.finance/api/router/v1/routes",
            params={
                "chainId": req.chain_id,
                "tokenIn": token_in,
                "tokenOut": token_out,
                "amountIn": str(req.amount_in),
                "router": "curve",
            },
        )
        transport = json_transport_outcome(call=call, provider_name="Curve")
        if transport.failure is not None:
            return QuoteResult(
                provider=self.id,
                status=transport.failure.status,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=transport.failure.latency_ms,
                error=error_from_status(transport.failure.status, transport.failure.message),
            )

        payload = transport.payload
        assert payload is not None
        latency_ms = transport.latency_ms

        data = payload.get("data")
        payload_obj = data if isinstance(data, dict) else payload

        amount_out_value = get_first(payload_obj, ["amountOut", "output", "toAmount"])
        if amount_out_value is None:
            amount_out_value = get_nested(payload_obj, ["bestRoute", "output"])

        amount_out = parse_int(amount_out_value)
        if amount_out is None:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.UNSUPPORTED_TOKEN,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=latency_ms,
                error=error_from_status(ProviderStatus.UNSUPPORTED_TOKEN, "No route found"),
            )

        gas_value = get_first(payload_obj, ["gas", "estimatedGas"])
        gas = parse_int(gas_value)

        price_impact = parse_decimal(get_first(payload_obj, ["priceImpact", "price_impact"]))
        price_impact_bps = decimal_to_bps(price_impact)

        timestamp_val = get_first(payload_obj, ["timestamp", "asOf"])

        route_data = get_first(payload_obj, ["route", "routes", "bestRoute"])
        route = route_data if isinstance(route_data, dict) else None

        return QuoteResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=amount_out,
            amount_out_min=parse_int(get_first(payload_obj, ["amountOutMin", "toAmountMin"])),
            price_impact_bps=price_impact_bps,
            estimated_gas=gas,
            latency_ms=latency_ms,
            as_of=parse_datetime(timestamp_val),
            route=route,
        )


def _to_curve_native_alias(address: str) -> str:
    if address.lower() == NATIVE_TOKEN_ALIAS.lower():
        return "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    return address

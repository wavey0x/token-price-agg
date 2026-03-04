from __future__ import annotations

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from token_price_agg.providers.base import ProviderPlugin
from token_price_agg.providers.clients.http import HttpClient
from token_price_agg.providers.common import first_nested_dict, payload_data_or_root
from token_price_agg.providers.http_helpers import json_transport_outcome, timed_get
from token_price_agg.providers.parsing import (
    decimal_to_bps,
    get_first,
    get_nested,
    parse_datetime,
    parse_decimal,
    parse_int,
    with_token_metadata,
)
from token_price_agg.providers.utils import error_from_status

_ENSO_DUMMY_ADDRESS = "0x0000000000000000000000000000000000000001"


class EnsoProvider(ProviderPlugin):
    id = "enso"
    supports_price = True
    supports_quote = True
    requires_api_key = True

    def __init__(
        self,
        *,
        client: HttpClient,
        api_key: str | None,
        available: bool,
        unavailable_reason: str | None = None,
    ) -> None:
        super().__init__(available=available, unavailable_reason=unavailable_reason)
        self._client = client
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        call = await timed_get(
            client=self._client,
            url=f"https://api.enso.finance/api/v1/prices/{req.chain_id}/{req.token.address}",
            headers=self._headers(),
        )
        transport = json_transport_outcome(call=call, provider_name="Enso")
        if transport.failure is not None:
            return PriceResult(
                provider=self.id,
                status=transport.failure.status,
                token=req.token,
                latency_ms=transport.failure.latency_ms,
                error=error_from_status(transport.failure.status, transport.failure.message),
            )

        response_payload = transport.payload
        assert response_payload is not None
        latency_ms = transport.latency_ms

        payload = payload_data_or_root(response_payload)

        price = parse_decimal(get_first(payload, ["price", "usdPrice", "priceUsd"]))
        if price is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UNSUPPORTED_TOKEN,
                token=req.token,
                latency_ms=latency_ms,
                error=error_from_status(ProviderStatus.UNSUPPORTED_TOKEN, "Token not supported"),
            )

        return PriceResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token=with_token_metadata(req.token, payload),
            price_usd=price,
            latency_ms=latency_ms,
            as_of=parse_datetime(get_first(payload, ["timestamp", "updatedAt"])),
        )

    async def get_quote(self, req: ProviderQuoteRequest) -> QuoteResult:
        call = await timed_get(
            client=self._client,
            url="https://api.enso.build/api/v1/shortcuts/route",
            params={
                "chainId": req.chain_id,
                "fromAddress": _ENSO_DUMMY_ADDRESS,
                "tokenIn": req.token_in.address,
                "tokenOut": req.token_out.address,
                "amountIn": str(req.amount_in),
                "slippage": 300,
            },
            headers=self._headers(),
        )
        transport = json_transport_outcome(call=call, provider_name="Enso")
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

        response_payload = transport.payload
        assert response_payload is not None
        latency_ms = transport.latency_ms

        payload = payload_data_or_root(response_payload)

        amount_out = parse_int(get_first(payload, ["amountOut", "toAmount", "outputAmount"]))
        if amount_out is None:
            amount_out = parse_int(get_nested(payload, ["route", "amountOut"]))

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

        price_impact_raw = parse_decimal(get_first(payload, ["priceImpact", "price_impact"]))
        price_impact_bps = decimal_to_bps(price_impact_raw)

        route_data = payload.get("route")
        route = route_data if isinstance(route_data, dict) else None
        token_in = with_token_metadata(
            req.token_in,
            first_nested_dict(payload, paths=[["tokenIn"], ["fromToken"], ["inputToken"]]),
        )
        token_out = with_token_metadata(
            req.token_out,
            first_nested_dict(payload, paths=[["tokenOut"], ["toToken"], ["outputToken"]]),
        )

        return QuoteResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token_in=token_in,
            token_out=token_out,
            amount_in=req.amount_in,
            amount_out=amount_out,
            amount_out_min=parse_int(
                get_first(payload, ["minAmountOut", "amountOutMin", "toAmountMin"])
            ),
            estimated_gas=parse_int(get_first(payload, ["gas", "estimatedGas"])),
            price_impact_bps=price_impact_bps,
            latency_ms=latency_ms,
            as_of=parse_datetime(get_first(payload, ["timestamp", "updatedAt"])),
            route=route,
        )

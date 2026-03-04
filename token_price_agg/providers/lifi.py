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
from token_price_agg.providers.common import first_nested_dict
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

_LIFI_DUMMY_ADDRESS = "0x0000000000000000000000000000000000000001"


class LiFiProvider(ProviderPlugin):
    id = "lifi"
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
        return {"x-lifi-api-key": self._api_key}

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        call = await timed_get(
            client=self._client,
            url="https://li.quest/v1/token",
            params={"chain": req.chain_id, "token": req.token.address},
            headers=self._headers(),
        )
        transport = json_transport_outcome(call=call, provider_name="LI.FI")
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

        price = parse_decimal(get_first(payload, ["priceUSD", "priceUsd", "price"]))
        if price is None:
            token_data = payload.get("token")
            if isinstance(token_data, dict):
                price = parse_decimal(get_first(token_data, ["priceUSD", "priceUsd", "price"]))

        if price is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UNSUPPORTED_TOKEN,
                token=req.token,
                latency_ms=latency_ms,
                error=error_from_status(ProviderStatus.UNSUPPORTED_TOKEN, "Token not supported"),
            )

        as_of = parse_datetime(get_first(payload, ["timestamp", "updatedAt"]))
        token_payload = payload.get("token")
        token = with_token_metadata(req.token, token_payload)

        return PriceResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token=token,
            price_usd=price,
            latency_ms=latency_ms,
            as_of=as_of,
        )

    async def get_quote(self, req: ProviderQuoteRequest) -> QuoteResult:
        call = await timed_get(
            client=self._client,
            url="https://li.quest/v1/quote",
            params={
                "fromChain": req.chain_id,
                "toChain": req.chain_id,
                "fromToken": req.token_in.address,
                "toToken": req.token_out.address,
                "fromAmount": str(req.amount_in),
                "fromAddress": _LIFI_DUMMY_ADDRESS,
                "toAddress": _LIFI_DUMMY_ADDRESS,
                "slippage": 0.003,
            },
            headers=self._headers(),
        )
        transport = json_transport_outcome(call=call, provider_name="LI.FI")
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

        amount_out = parse_int(get_nested(payload, ["estimate", "toAmount"]))
        if amount_out is None:
            amount_out = parse_int(get_first(payload, ["toAmount", "amountOut"]))

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

        min_out = parse_int(get_nested(payload, ["estimate", "toAmountMin"]))
        gas = parse_int(get_nested(payload, ["estimate", "data", "estimatedGas"]))

        price_impact = parse_decimal(get_nested(payload, ["estimate", "priceImpact"]))
        price_impact_bps = decimal_to_bps(price_impact)

        route_obj = payload.get("route")
        route = route_obj if isinstance(route_obj, dict) else None

        as_of = parse_datetime(get_first(payload, ["timestamp", "updatedAt"]))
        token_in = with_token_metadata(
            req.token_in,
            first_nested_dict(
                payload,
                paths=[
                    ["action", "fromToken"],
                    ["estimate", "fromToken"],
                    ["fromToken"],
                ],
            ),
        )
        token_out = with_token_metadata(
            req.token_out,
            first_nested_dict(
                payload,
                paths=[
                    ["action", "toToken"],
                    ["estimate", "toToken"],
                    ["toToken"],
                ],
            ),
        )

        return QuoteResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token_in=token_in,
            token_out=token_out,
            amount_in=req.amount_in,
            amount_out=amount_out,
            amount_out_min=min_out,
            price_impact_bps=price_impact_bps,
            estimated_gas=gas,
            latency_ms=latency_ms,
            as_of=as_of,
            route=route,
        )

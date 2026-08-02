from __future__ import annotations

from price_api.core.errors import ErrorInfo, ErrorType, ProviderStatus
from price_api.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from price_api.providers.base import ProviderPlugin
from price_api.providers.clients.http import HttpClient, QueryParams
from price_api.providers.common import first_nested_dict
from price_api.providers.http_helpers import json_transport_outcome, timed_get
from price_api.providers.parsing import (
    decimal_to_bps,
    get_first,
    get_nested,
    parse_base_unit_amount,
    parse_datetime,
    parse_decimal,
    parse_int,
    parse_positive_decimal,
    with_token_metadata,
)

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
        deny_exchanges: list[str] | None = None,
        available: bool,
        unavailable_reason: str | None = None,
    ) -> None:
        super().__init__(available=available, unavailable_reason=unavailable_reason)
        self._client = client
        self._api_key = api_key
        self._deny_exchanges = tuple(deny_exchanges or [])

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
            timeout_ms=req.timeout_ms,
            provider_id=self.id,
            operation="price",
        )
        transport = json_transport_outcome(call=call, provider_name="LI.FI")
        if transport.failure is not None:
            return PriceResult(
                provider=self.id,
                status=transport.failure.status,
                token=req.token,
                latency_ms=transport.failure.latency_ms,
                error=transport.failure.to_error_info(),
            )

        payload = transport.payload
        assert payload is not None
        latency_ms = transport.latency_ms

        price = parse_positive_decimal(get_first(payload, ["priceUSD", "priceUsd", "price"]))
        if price is None:
            token_data = payload.get("token")
            if isinstance(token_data, dict):
                price = parse_positive_decimal(
                    get_first(token_data, ["priceUSD", "priceUsd", "price"])
                )

        if price is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.NO_ROUTE,
                token=req.token,
                latency_ms=latency_ms,
                error=ErrorInfo(type=ErrorType.NO_ROUTE, message="Token not supported"),
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
        params: QueryParams = {
            "fromChain": req.chain_id,
            "toChain": req.chain_id,
            "fromToken": req.token_in.address,
            "toToken": req.token_out.address,
            "fromAmount": str(req.amount_in),
            "fromAddress": _LIFI_DUMMY_ADDRESS,
            "toAddress": _LIFI_DUMMY_ADDRESS,
            "slippage": 0.003,
        }
        if self._deny_exchanges:
            params["denyExchanges"] = ",".join(self._deny_exchanges)

        call = await timed_get(
            client=self._client,
            url="https://li.quest/v1/quote",
            params=params,
            headers=self._headers(),
            timeout_ms=req.timeout_ms,
            provider_id=self.id,
            operation="quote",
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
                error=transport.failure.to_error_info(),
            )

        payload = transport.payload
        assert payload is not None
        latency_ms = transport.latency_ms
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
        token_out_decimals = token_out.decimals

        amount_out = parse_base_unit_amount(
            get_nested(payload, ["estimate", "toAmount"]),
            token_decimals=token_out_decimals,
        )
        if amount_out is None or amount_out <= 0:
            amount_out = parse_base_unit_amount(
                get_first(payload, ["toAmount", "amountOut"]),
                token_decimals=token_out_decimals,
            )

        if amount_out is None or amount_out <= 0:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.NO_ROUTE,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=latency_ms,
                error=ErrorInfo(type=ErrorType.NO_ROUTE, message="No route found"),
            )

        min_out = parse_base_unit_amount(
            get_nested(payload, ["estimate", "toAmountMin"]),
            token_decimals=token_out_decimals,
        )
        gas = parse_int(get_nested(payload, ["estimate", "data", "estimatedGas"]))

        price_impact = parse_decimal(get_nested(payload, ["estimate", "priceImpact"]))
        price_impact_bps = decimal_to_bps(price_impact)

        route_obj = payload.get("route")
        route = route_obj if isinstance(route_obj, dict) else None

        as_of = parse_datetime(get_first(payload, ["timestamp", "updatedAt"]))

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

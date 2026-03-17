from __future__ import annotations

from token_price_agg.core.errors import ErrorCode, ErrorInfo, ProviderStatus
from token_price_agg.core.models import PriceResult, ProviderPriceRequest
from token_price_agg.providers.base import ProviderPlugin
from token_price_agg.providers.clients.http import HttpClient
from token_price_agg.providers.http_helpers import (
    json_transport_outcome,
    timed_get,
)
from token_price_agg.providers.parsing import (
    parse_datetime,
    parse_decimal,
    with_token_metadata,
)


class DefiLlamaProvider(ProviderPlugin):
    id = "defillama"
    supports_price = True
    supports_quote = False

    def __init__(self, *, client: HttpClient, available: bool = True) -> None:
        super().__init__(available=available)
        self._client = client

    def _price_url(self, coin: str) -> str:
        return f"https://coins.llama.fi/prices/current/{coin}"

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        coin = f"ethereum:{req.token.address}"

        call = await timed_get(
            client=self._client,
            url=self._price_url(coin),
            params={"searchWidth": "4h"},
            timeout_ms=req.timeout_ms,
        )
        transport = json_transport_outcome(
            call=call,
            provider_name="DefiLlama",
            invalid_json_message="Invalid DefiLlama JSON response",
        )
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

        coins = payload.get("coins")
        if not isinstance(coins, dict):
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.NO_ROUTE,
                token=req.token,
                latency_ms=latency_ms,
                error=ErrorInfo(code=ErrorCode.NO_ROUTE, message="Token not found"),
            )

        coin_data = coins.get(coin)
        if not isinstance(coin_data, dict):
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.NO_ROUTE,
                token=req.token,
                latency_ms=latency_ms,
                error=ErrorInfo(code=ErrorCode.NO_ROUTE, message="Token not found"),
            )

        price = parse_decimal(coin_data.get("price"))
        as_of = parse_datetime(coin_data.get("timestamp"))

        if price is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.ERROR,
                token=req.token,
                latency_ms=latency_ms,
                error=ErrorInfo(
                    code=ErrorCode.UPSTREAM_PARSE,
                    message="Price missing from response",
                ),
            )

        token = with_token_metadata(req.token, coin_data)

        return PriceResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token=token,
            price_usd=price,
            latency_ms=latency_ms,
            as_of=as_of,
        )

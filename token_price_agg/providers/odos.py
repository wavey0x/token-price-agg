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
from token_price_agg.providers.clients.http import HttpClient, HttpResponse
from token_price_agg.providers.http_helpers import timed_get, timed_post
from token_price_agg.providers.parsing import decimal_to_bps, parse_decimal, parse_int
from token_price_agg.providers.utils import error_from_status, status_from_http_code

_ODOS_NATIVE_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"
_UNSUPPORTED_TOKEN_DETAIL_MARKERS = (
    "no price available for this chain and address",
    "routing unavailable for token",
    "no route",
)


class OdosProvider(ProviderPlugin):
    id = "odos"
    supports_price = True
    supports_quote = True

    def __init__(self, *, client: HttpClient, available: bool = True) -> None:
        super().__init__(available=available)
        self._client = client

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        call = await timed_get(
            client=self._client,
            url=(
                f"https://api.odos.xyz/pricing/token/{req.chain_id}/"
                f"{_to_odos_token_address(req.token.address)}"
            ),
            headers={"accept": "application/json"},
            timeout_ms=req.timeout_ms,
        )
        if call.timeout:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.TIMEOUT,
                token=req.token,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.TIMEOUT, "ODOS request timed out"),
            )
        if call.http_error is not None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token=req.token,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.UPSTREAM_ERROR, str(call.http_error)),
            )

        response = call.response
        if response is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token=req.token,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.UPSTREAM_ERROR, "ODOS response missing"),
            )

        if response.status_code != 200:
            status, detail = _status_and_detail_from_error(response)
            return PriceResult(
                provider=self.id,
                status=status,
                token=req.token,
                latency_ms=call.latency_ms,
                error=error_from_status(status, detail),
            )

        payload = response.json_data
        if not isinstance(payload, dict):
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token=req.token,
                latency_ms=call.latency_ms,
                error=error_from_status(
                    ProviderStatus.UPSTREAM_ERROR,
                    "Invalid ODOS JSON response",
                ),
            )

        price = parse_decimal(payload.get("price"))
        if price is None:
            return PriceResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token=req.token,
                latency_ms=call.latency_ms,
                error=error_from_status(
                    ProviderStatus.UPSTREAM_ERROR,
                    "Price missing from response",
                ),
            )

        return PriceResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token=req.token,
            price_usd=price,
            latency_ms=call.latency_ms,
        )

    async def get_quote(self, req: ProviderQuoteRequest) -> QuoteResult:
        call = await timed_post(
            client=self._client,
            url="https://api.odos.xyz/sor/quote/v3",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=_build_quote_payload(req),
            timeout_ms=req.timeout_ms,
        )
        if call.timeout:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.TIMEOUT,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.TIMEOUT, "ODOS request timed out"),
            )
        if call.http_error is not None:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.UPSTREAM_ERROR, str(call.http_error)),
            )

        response = call.response
        if response is None:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.UPSTREAM_ERROR, "ODOS response missing"),
            )

        if response.status_code != 200:
            status, detail = _status_and_detail_from_error(response)
            return QuoteResult(
                provider=self.id,
                status=status,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=call.latency_ms,
                error=error_from_status(status, detail),
            )

        payload = response.json_data
        if not isinstance(payload, dict):
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.UPSTREAM_ERROR,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=call.latency_ms,
                error=error_from_status(
                    ProviderStatus.UPSTREAM_ERROR,
                    "Invalid ODOS JSON response",
                ),
            )

        amount_out = _parse_first_amount(payload.get("outAmounts"))
        if amount_out is None:
            return QuoteResult(
                provider=self.id,
                status=ProviderStatus.UNSUPPORTED_TOKEN,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=call.latency_ms,
                error=error_from_status(ProviderStatus.UNSUPPORTED_TOKEN, "No route found"),
            )

        price_impact_bps = decimal_to_bps(parse_decimal(payload.get("priceImpact")))
        route = _minimal_route_metadata(payload)

        return QuoteResult(
            provider=self.id,
            status=ProviderStatus.OK,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            amount_out=amount_out,
            amount_out_min=None,
            price_impact_bps=price_impact_bps,
            estimated_gas=parse_int(payload.get("gasEstimate")),
            latency_ms=call.latency_ms,
            route=route,
        )


def _to_odos_token_address(address: str) -> str:
    if address.lower() == NATIVE_TOKEN_ALIAS.lower():
        return _ODOS_NATIVE_TOKEN_ADDRESS
    return address


def _build_quote_payload(req: ProviderQuoteRequest) -> dict[str, object]:
    return {
        "chainId": req.chain_id,
        "inputTokens": [
            {
                "tokenAddress": _to_odos_token_address(req.token_in.address),
                "amount": str(req.amount_in),
            }
        ],
        "outputTokens": [
            {
                "tokenAddress": _to_odos_token_address(req.token_out.address),
                "proportion": 1,
            }
        ],
        "slippageLimitPercent": 0.3,
        "compact": True,
    }


def _status_and_detail_from_error(response: HttpResponse) -> tuple[ProviderStatus, str]:
    detail = _extract_error_detail(response)
    status = status_from_http_code(response.status_code)
    if response.status_code == 400 and _is_unsupported_token_detail(detail):
        status = ProviderStatus.UNSUPPORTED_TOKEN
    message = detail or f"ODOS returned {response.status_code}"
    return status, message


def _extract_error_detail(response: HttpResponse) -> str | None:
    payload = response.json_data
    if not isinstance(payload, dict):
        return None

    detail = payload.get("detail")
    if not isinstance(detail, str):
        return None

    stripped = detail.strip()
    if not stripped:
        return None

    error_code = payload.get("errorCode")
    parsed_code = parse_int(error_code)
    if parsed_code is None:
        return stripped
    return f"{stripped} (errorCode: {parsed_code})"


def _is_unsupported_token_detail(detail: str | None) -> bool:
    if detail is None:
        return False
    normalized = detail.lower()
    return any(marker in normalized for marker in _UNSUPPORTED_TOKEN_DETAIL_MARKERS)


def _parse_first_amount(value: object) -> int | None:
    if isinstance(value, list):
        for item in value:
            parsed = parse_int(item)
            if parsed is not None:
                return parsed
        return None
    return parse_int(value)


def _minimal_route_metadata(payload: dict[str, object]) -> dict[str, object] | None:
    route: dict[str, object] = {}
    for key in ("pathId", "blockNumber", "gasEstimate", "priceImpact", "percentDiff"):
        if key in payload:
            route[key] = payload[key]
    return route or None

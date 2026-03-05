from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import httpx

from token_price_agg.core.errors import ProviderStatus
from token_price_agg.providers.clients.http import HttpClient, HttpResponse, JsonBody, QueryParams
from token_price_agg.providers.utils import status_from_http_code

FailureReason = Literal["timeout", "http_error", "non_200", "invalid_json"]


@dataclass(frozen=True, slots=True)
class HttpCallResult:
    latency_ms: int
    response: HttpResponse | None = None
    timeout: bool = False
    http_error: httpx.HTTPError | None = None


@dataclass(frozen=True, slots=True)
class ProviderTransportFailure:
    reason: FailureReason
    status: ProviderStatus
    message: str
    latency_ms: int


@dataclass(frozen=True, slots=True)
class JsonTransportOutcome:
    latency_ms: int
    payload: dict[str, object] | None = None
    failure: ProviderTransportFailure | None = None


async def timed_get(
    *,
    client: HttpClient,
    url: str,
    params: QueryParams | None = None,
    headers: dict[str, str] | None = None,
) -> HttpCallResult:
    started = time.perf_counter()
    try:
        response = await client.get(url=url, params=params, headers=headers)
    except httpx.TimeoutException:
        return HttpCallResult(
            latency_ms=int((time.perf_counter() - started) * 1000),
            timeout=True,
        )
    except httpx.HTTPError as exc:
        return HttpCallResult(
            latency_ms=int((time.perf_counter() - started) * 1000),
            http_error=exc,
        )

    return HttpCallResult(
        latency_ms=int((time.perf_counter() - started) * 1000),
        response=response,
    )


async def timed_post(
    *,
    client: HttpClient,
    url: str,
    json: JsonBody | None = None,
    params: QueryParams | None = None,
    headers: dict[str, str] | None = None,
) -> HttpCallResult:
    started = time.perf_counter()
    try:
        response = await client.post(url=url, json=json, params=params, headers=headers)
    except httpx.TimeoutException:
        return HttpCallResult(
            latency_ms=int((time.perf_counter() - started) * 1000),
            timeout=True,
        )
    except httpx.HTTPError as exc:
        return HttpCallResult(
            latency_ms=int((time.perf_counter() - started) * 1000),
            http_error=exc,
        )

    return HttpCallResult(
        latency_ms=int((time.perf_counter() - started) * 1000),
        response=response,
    )


def json_transport_outcome(
    *,
    call: HttpCallResult,
    provider_name: str,
    invalid_json_message: str = "Invalid JSON response",
) -> JsonTransportOutcome:
    if call.timeout:
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="timeout",
                status=ProviderStatus.TIMEOUT,
                message=f"{provider_name} request timed out",
                latency_ms=call.latency_ms,
            ),
        )

    if call.http_error is not None:
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="http_error",
                status=ProviderStatus.UPSTREAM_ERROR,
                message=str(call.http_error),
                latency_ms=call.latency_ms,
            ),
        )

    response = call.response
    if response is None:
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="http_error",
                status=ProviderStatus.UPSTREAM_ERROR,
                message=f"{provider_name} response missing",
                latency_ms=call.latency_ms,
            ),
        )

    failure = non_200_status(response=response, provider_name=provider_name)
    if failure is not None:
        status, message = failure
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="non_200",
                status=status,
                message=message,
                latency_ms=call.latency_ms,
            ),
        )

    payload, invalid_json_error = expect_json_dict(
        response=response,
        invalid_json_message=invalid_json_message,
    )
    if payload is None:
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="invalid_json",
                status=ProviderStatus.UPSTREAM_ERROR,
                message=str(invalid_json_error),
                latency_ms=call.latency_ms,
            ),
        )

    return JsonTransportOutcome(latency_ms=call.latency_ms, payload=payload)


def non_200_status(
    *,
    response: HttpResponse,
    provider_name: str,
) -> tuple[ProviderStatus, str] | None:
    if response.status_code == 200:
        return None
    status = status_from_http_code(response.status_code)
    return status, f"{provider_name} returned {response.status_code}"


def expect_json_dict(
    *,
    response: HttpResponse,
    invalid_json_message: str = "Invalid JSON response",
) -> tuple[dict[str, object] | None, str | None]:
    if isinstance(response.json_data, dict):
        return response.json_data, None
    return None, invalid_json_message

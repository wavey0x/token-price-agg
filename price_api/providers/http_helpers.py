from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx

from price_api.core.errors import ErrorInfo, ErrorType, ProviderStatus
from price_api.observability.metrics import record_provider_pool_timeout
from price_api.providers.clients.http import HttpClient, HttpResponse, JsonBody, QueryParams
from price_api.providers.utils import status_from_http_code

_LOGGER = logging.getLogger("price_api.providers.transport")

FailureReason = Literal["timeout", "http_error", "non_200", "invalid_json"]


@dataclass(frozen=True, slots=True)
class HttpCallResult:
    latency_ms: int
    response: HttpResponse | None = None
    timeout: bool = False
    timeout_error_type: ErrorType | None = None
    transport_error_type: str | None = None
    http_error: httpx.HTTPError | None = None


@dataclass(frozen=True, slots=True)
class ProviderTransportFailure:
    reason: FailureReason
    status: ProviderStatus
    error_type: ErrorType
    message: str
    latency_ms: int
    http_status_code: int | None = None
    retry_after_ms: int | None = None

    def to_error_info(self) -> ErrorInfo:
        return ErrorInfo(
            type=self.error_type,
            message=self.message,
            code=self.http_status_code,
            retry_after_ms=self.retry_after_ms,
        )


@dataclass(frozen=True, slots=True)
class JsonTransportOutcome:
    latency_ms: int
    payload: dict[str, object] | None = None
    failure: ProviderTransportFailure | None = None


@dataclass(frozen=True, slots=True)
class HttpStatusFailure:
    status: ProviderStatus
    error_type: ErrorType
    message: str
    http_status_code: int
    retry_after_ms: int | None = None

    def to_error_info(self) -> ErrorInfo:
        return ErrorInfo(
            type=self.error_type,
            message=self.message,
            code=self.http_status_code,
            retry_after_ms=self.retry_after_ms,
        )


async def timed_get(
    *,
    client: HttpClient,
    url: str,
    params: QueryParams | None = None,
    headers: dict[str, str] | None = None,
    timeout_ms: int | None = None,
    provider_id: str | None = None,
    operation: str | None = None,
) -> HttpCallResult:
    started = time.perf_counter()
    try:
        response = await client.get(url=url, params=params, headers=headers, timeout_ms=timeout_ms)
    except httpx.PoolTimeout as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        record_provider_pool_timeout(
            provider=provider_id or "unknown",
            operation=operation or "unknown",
        )
        await client.record_pool_timeout()
        _log_transport_failure(
            client=client,
            exc=exc,
            provider_id=provider_id,
            operation=operation,
            timeout_ms=timeout_ms,
        )
        return HttpCallResult(
            latency_ms=latency_ms,
            timeout=True,
            timeout_error_type=ErrorType.INTERNAL_TRANSPORT_TIMEOUT,
            transport_error_type=type(exc).__name__,
        )
    except httpx.TimeoutException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_transport_failure(
            client=client,
            exc=exc,
            provider_id=provider_id,
            operation=operation,
            timeout_ms=timeout_ms,
        )
        return HttpCallResult(
            latency_ms=latency_ms,
            timeout=True,
            timeout_error_type=ErrorType.TIMEOUT,
            transport_error_type=type(exc).__name__,
        )
    except httpx.HTTPError as exc:
        _log_transport_failure(
            client=client,
            exc=exc,
            provider_id=provider_id,
            operation=operation,
            timeout_ms=timeout_ms,
        )
        return HttpCallResult(
            latency_ms=int((time.perf_counter() - started) * 1000),
            transport_error_type=type(exc).__name__,
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
    timeout_ms: int | None = None,
    provider_id: str | None = None,
    operation: str | None = None,
) -> HttpCallResult:
    started = time.perf_counter()
    try:
        response = await client.post(
            url=url,
            json=json,
            params=params,
            headers=headers,
            timeout_ms=timeout_ms,
        )
    except httpx.PoolTimeout as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        record_provider_pool_timeout(
            provider=provider_id or "unknown",
            operation=operation or "unknown",
        )
        await client.record_pool_timeout()
        _log_transport_failure(
            client=client,
            exc=exc,
            provider_id=provider_id,
            operation=operation,
            timeout_ms=timeout_ms,
        )
        return HttpCallResult(
            latency_ms=latency_ms,
            timeout=True,
            timeout_error_type=ErrorType.INTERNAL_TRANSPORT_TIMEOUT,
            transport_error_type=type(exc).__name__,
        )
    except httpx.TimeoutException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _log_transport_failure(
            client=client,
            exc=exc,
            provider_id=provider_id,
            operation=operation,
            timeout_ms=timeout_ms,
        )
        return HttpCallResult(
            latency_ms=latency_ms,
            timeout=True,
            timeout_error_type=ErrorType.TIMEOUT,
            transport_error_type=type(exc).__name__,
        )
    except httpx.HTTPError as exc:
        _log_transport_failure(
            client=client,
            exc=exc,
            provider_id=provider_id,
            operation=operation,
            timeout_ms=timeout_ms,
        )
        return HttpCallResult(
            latency_ms=int((time.perf_counter() - started) * 1000),
            transport_error_type=type(exc).__name__,
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
        error = timeout_error_info(call=call, provider_name=provider_name)
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="timeout",
                status=ProviderStatus.ERROR,
                error_type=error.type,
                message=error.message,
                latency_ms=call.latency_ms,
            ),
        )

    if call.http_error is not None:
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="http_error",
                status=ProviderStatus.ERROR,
                error_type=ErrorType.UPSTREAM_HTTP,
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
                status=ProviderStatus.ERROR,
                error_type=ErrorType.UPSTREAM_HTTP,
                message=f"{provider_name} response missing",
                latency_ms=call.latency_ms,
            ),
        )

    failure = non_200_status(response=response, provider_name=provider_name)
    if failure is not None:
        return JsonTransportOutcome(
            latency_ms=call.latency_ms,
            failure=ProviderTransportFailure(
                reason="non_200",
                status=failure.status,
                error_type=failure.error_type,
                message=failure.message,
                latency_ms=call.latency_ms,
                http_status_code=failure.http_status_code,
                retry_after_ms=failure.retry_after_ms,
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
                status=ProviderStatus.ERROR,
                error_type=ErrorType.UPSTREAM_PARSE,
                message=str(invalid_json_error),
                latency_ms=call.latency_ms,
            ),
        )

    return JsonTransportOutcome(latency_ms=call.latency_ms, payload=payload)


def timeout_error_info(*, call: HttpCallResult, provider_name: str) -> ErrorInfo:
    error_type = call.timeout_error_type or ErrorType.TIMEOUT
    if error_type == ErrorType.INTERNAL_TRANSPORT_TIMEOUT:
        return ErrorInfo(
            type=error_type,
            message=(
                f"{provider_name} internal transport timed out before acquiring outbound capacity"
            ),
        )
    return ErrorInfo(type=error_type, message=f"{provider_name} request timed out")


def _log_transport_failure(
    *,
    client: HttpClient,
    exc: httpx.HTTPError,
    provider_id: str | None,
    operation: str | None,
    timeout_ms: int | None,
) -> None:
    _LOGGER.warning(
        "provider_transport_failure",
        extra={
            "provider": provider_id or "unknown",
            "operation": operation or "unknown",
            "transport_error_type": type(exc).__name__,
            "timeout_ms": timeout_ms if timeout_ms is not None else client.timeout_ms,
            "provider_global_limit": client.max_connections,
        },
    )


def non_200_status(
    *,
    response: HttpResponse,
    provider_name: str,
) -> HttpStatusFailure | None:
    if response.status_code == 200:
        return None
    status, error_type = status_from_http_code(response.status_code)
    return HttpStatusFailure(
        status=status,
        error_type=error_type,
        message=f"{provider_name} returned {response.status_code}",
        http_status_code=response.status_code,
        retry_after_ms=parse_retry_after_ms(response),
    )


def parse_retry_after_ms(
    response: HttpResponse,
    *,
    now: datetime | None = None,
) -> int | None:
    retry_after = response.headers.get("retry-after")
    if retry_after is None:
        return None

    stripped = retry_after.strip()
    if not stripped:
        return None

    try:
        seconds = int(stripped)
    except ValueError:
        retry_at = _parse_http_date(stripped)
        if retry_at is None:
            return None
        current = now or datetime.now(tz=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        delta_ms = int((retry_at - current).total_seconds() * 1000)
        return delta_ms if delta_ms > 0 else None

    if seconds < 0:
        return None
    return seconds * 1000


def _parse_http_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def expect_json_dict(
    *,
    response: HttpResponse,
    invalid_json_message: str = "Invalid JSON response",
) -> tuple[dict[str, object] | None, str | None]:
    if isinstance(response.json_data, dict):
        return response.json_data, None
    return None, invalid_json_message

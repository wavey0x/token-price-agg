from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS_TOTAL = Counter(
    "token_price_agg_http_requests_total",
    "Total HTTP requests",
    labelnames=("endpoint", "method", "status_class"),
)

HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "token_price_agg_http_request_latency_seconds",
    "HTTP request latency in seconds",
    labelnames=("endpoint", "method"),
)

HTTP_INFLIGHT_REQUESTS = Gauge(
    "token_price_agg_http_inflight_requests",
    "Number of in-flight HTTP requests",
)

PARTIAL_RESPONSES_TOTAL = Counter(
    "token_price_agg_partial_responses_total",
    "Total partial responses from aggregator endpoints",
    labelnames=("endpoint",),
)

ALL_FAILED_RESPONSES_TOTAL = Counter(
    "token_price_agg_all_failed_responses_total",
    "Total responses where all selected providers failed",
    labelnames=("endpoint",),
)

PROVIDER_CALLS_TOTAL = Counter(
    "token_price_agg_provider_calls_total",
    "Total provider calls",
    labelnames=("provider", "operation", "status"),
)

PROVIDER_CALL_LATENCY_SECONDS = Histogram(
    "token_price_agg_provider_call_latency_seconds",
    "Provider call latency in seconds",
    labelnames=("provider", "operation"),
)

PROVIDER_AVAILABLE = Gauge(
    "token_price_agg_provider_available",
    "Provider availability gauge (1=available, 0=unavailable)",
    labelnames=("provider",),
)

VAULT_RESOLUTION_TOTAL = Counter(
    "token_price_agg_vault_resolution_total",
    "Vault resolution attempts",
    labelnames=("result", "vault_type"),
)

VAULT_RESOLUTION_LATENCY_SECONDS = Histogram(
    "token_price_agg_vault_resolution_latency_seconds",
    "Vault resolution latency in seconds",
    labelnames=("vault_type",),
)

AUTH_TOTAL = Counter(
    "token_price_agg_auth_total",
    "Authentication decisions for protected API endpoints",
    labelnames=("result",),
)

RATE_LIMIT_TOTAL = Counter(
    "token_price_agg_rate_limit_total",
    "Total requests rejected by API key rate limiting",
    labelnames=("endpoint",),
)


def observe_http_request(
    *,
    endpoint: str,
    method: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    status_class = f"{status_code // 100}xx"
    HTTP_REQUESTS_TOTAL.labels(endpoint=endpoint, method=method, status_class=status_class).inc()
    HTTP_REQUEST_LATENCY_SECONDS.labels(endpoint=endpoint, method=method).observe(duration_seconds)


def inc_inflight_request() -> None:
    HTTP_INFLIGHT_REQUESTS.inc()


def dec_inflight_request() -> None:
    HTTP_INFLIGHT_REQUESTS.dec()


def record_partial_response(*, endpoint: str) -> None:
    PARTIAL_RESPONSES_TOTAL.labels(endpoint=endpoint).inc()


def record_all_failed_response(*, endpoint: str) -> None:
    ALL_FAILED_RESPONSES_TOTAL.labels(endpoint=endpoint).inc()


def record_provider_call(
    *,
    provider: str,
    operation: str,
    status: str,
    latency_ms: int,
) -> None:
    PROVIDER_CALLS_TOTAL.labels(provider=provider, operation=operation, status=status).inc()
    PROVIDER_CALL_LATENCY_SECONDS.labels(provider=provider, operation=operation).observe(
        max(latency_ms, 0) / 1000
    )


def set_provider_available(*, provider: str, available: bool) -> None:
    PROVIDER_AVAILABLE.labels(provider=provider).set(1 if available else 0)


def record_vault_resolution(*, result: str, vault_type: str, duration_seconds: float) -> None:
    VAULT_RESOLUTION_TOTAL.labels(result=result, vault_type=vault_type).inc()
    VAULT_RESOLUTION_LATENCY_SECONDS.labels(vault_type=vault_type).observe(duration_seconds)


def record_auth_result(*, result: str) -> None:
    AUTH_TOTAL.labels(result=result).inc()


def record_rate_limited(*, endpoint: str) -> None:
    RATE_LIMIT_TOTAL.labels(endpoint=endpoint).inc()

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_KNOWN_ENDPOINTS = {
    "/v1/price",
    "/v1/quote",
    "/v1/health",
    "/v1/ready",
    "/v1/providers",
    "/v1/token",
    "/metrics",
    "/token-logos/{chain_id}/{address}",
}
_KNOWN_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

HTTP_REQUESTS_TOTAL = Counter(
    "price_api_http_requests_total",
    "Total HTTP requests",
    labelnames=("endpoint", "method", "status_class"),
)

HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "price_api_http_request_latency_seconds",
    "HTTP request latency in seconds",
    labelnames=("endpoint", "method"),
)

HTTP_INFLIGHT_REQUESTS = Gauge(
    "price_api_http_inflight_requests",
    "Number of in-flight HTTP requests",
)

PARTIAL_RESPONSES_TOTAL = Counter(
    "price_api_partial_responses_total",
    "Total partial responses from aggregator endpoints",
    labelnames=("endpoint",),
)

ALL_FAILED_RESPONSES_TOTAL = Counter(
    "price_api_all_failed_responses_total",
    "Total responses where all selected providers failed",
    labelnames=("endpoint",),
)

PROVIDER_CALLS_TOTAL = Counter(
    "price_api_provider_calls_total",
    "Total provider calls",
    labelnames=("provider", "operation", "status"),
)

PROVIDER_CALL_LATENCY_SECONDS = Histogram(
    "price_api_provider_call_latency_seconds",
    "Provider call latency in seconds",
    labelnames=("provider", "operation"),
)

PROVIDER_AVAILABLE = Gauge(
    "price_api_provider_available",
    "Provider availability gauge (1=available, 0=unavailable)",
    labelnames=("provider",),
)

VAULT_RESOLUTION_TOTAL = Counter(
    "price_api_vault_resolution_total",
    "Vault resolution attempts",
    labelnames=("result", "vault_type"),
)

VAULT_RESOLUTION_LATENCY_SECONDS = Histogram(
    "price_api_vault_resolution_latency_seconds",
    "Vault resolution latency in seconds",
    labelnames=("vault_type",),
)

AUTH_TOTAL = Counter(
    "price_api_auth_total",
    "Authentication decisions for protected API endpoints",
    labelnames=("result",),
)

RATE_LIMIT_TOTAL = Counter(
    "price_api_rate_limit_total",
    "Total requests rejected by API key rate limiting",
    labelnames=("endpoint",),
)

ADMISSION_REJECTIONS_TOTAL = Counter(
    "price_api_admission_rejections_total",
    "Total requests rejected by admission control",
    labelnames=("reason", "operation"),
)

ADMISSION_INFLIGHT_UNITS = Gauge(
    "price_api_admission_inflight_units",
    "In-flight capacity units reserved by admission control",
    labelnames=("scope", "operation"),
)

PROVIDER_INFLIGHT_CALLS = Gauge(
    "price_api_provider_inflight_calls",
    "In-flight provider calls",
    labelnames=("provider", "operation"),
)

PROVIDER_POOL_TIMEOUTS_TOTAL = Counter(
    "price_api_provider_pool_timeouts_total",
    "Provider calls that timed out before acquiring a local HTTP pool slot",
    labelnames=("provider", "operation"),
)

PROVIDER_TRANSPORT_RECYCLES_TOTAL = Counter(
    "price_api_provider_transport_recycles_total",
    "Provider HTTP client recycle events",
    labelnames=("provider", "reason"),
)

PROVIDER_CIRCUIT_STATE = Gauge(
    "price_api_provider_circuit_state",
    "Provider circuit state gauge (0=closed, 1=half_open, 2=open)",
    labelnames=("provider",),
)

PROVIDER_CIRCUIT_TRANSITIONS_TOTAL = Counter(
    "price_api_provider_circuit_transitions_total",
    "Provider circuit state transitions",
    labelnames=("provider", "state"),
)

PROCESS_CLOSE_WAIT_SOCKETS = Gauge(
    "price_api_process_close_wait_sockets",
    "CLOSE-WAIT sockets owned by this process",
)

TOKEN_LOGO_PUBLIC_TOTAL = Counter(
    "price_api_token_logo_public_total",
    "Public token-logo reads",
    labelnames=("result",),
)

TOKEN_LOGO_ACQUISITION_TOTAL = Counter(
    "price_api_token_logo_acquisition_total",
    "Token-logo acquisition outcomes",
    labelnames=("outcome", "source"),
)

TOKEN_LOGO_DUE = Gauge(
    "price_api_token_logo_due",
    "Token-logo identities currently due for acquisition",
)

TOKEN_LOGO_ACQUISITION_ACTIVE = Gauge(
    "price_api_token_logo_acquisition_active",
    "Active token-logo acquisitions",
)

TOKEN_LOGO_SOURCE_REFRESH_AGE_SECONDS = Gauge(
    "price_api_token_logo_source_refresh_age_seconds",
    "Age of the latest successful token-logo source refresh",
    labelnames=("source", "chain_id"),
)


def observe_http_request(
    *,
    endpoint: str,
    method: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    endpoint = normalize_endpoint(endpoint)
    method = normalize_method(method)
    status_class = f"{status_code // 100}xx"
    HTTP_REQUESTS_TOTAL.labels(endpoint=endpoint, method=method, status_class=status_class).inc()
    HTTP_REQUEST_LATENCY_SECONDS.labels(endpoint=endpoint, method=method).observe(duration_seconds)


def inc_inflight_request() -> None:
    HTTP_INFLIGHT_REQUESTS.inc()


def dec_inflight_request() -> None:
    HTTP_INFLIGHT_REQUESTS.dec()


def record_partial_response(*, endpoint: str) -> None:
    endpoint = normalize_endpoint(endpoint)
    PARTIAL_RESPONSES_TOTAL.labels(endpoint=endpoint).inc()


def record_all_failed_response(*, endpoint: str) -> None:
    endpoint = normalize_endpoint(endpoint)
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
    endpoint = normalize_endpoint(endpoint)
    RATE_LIMIT_TOTAL.labels(endpoint=endpoint).inc()


def record_admission_rejection(*, reason: str, operation: str) -> None:
    ADMISSION_REJECTIONS_TOTAL.labels(reason=reason, operation=operation).inc()


def set_admission_inflight_units(*, scope: str, operation: str, units: int) -> None:
    ADMISSION_INFLIGHT_UNITS.labels(scope=scope, operation=operation).set(units)


def inc_provider_inflight_call(*, provider: str, operation: str) -> None:
    PROVIDER_INFLIGHT_CALLS.labels(provider=provider, operation=operation).inc()


def dec_provider_inflight_call(*, provider: str, operation: str) -> None:
    PROVIDER_INFLIGHT_CALLS.labels(provider=provider, operation=operation).dec()


def record_provider_pool_timeout(*, provider: str, operation: str) -> None:
    PROVIDER_POOL_TIMEOUTS_TOTAL.labels(provider=provider, operation=operation).inc()


def record_provider_transport_recycle(*, provider: str, reason: str) -> None:
    PROVIDER_TRANSPORT_RECYCLES_TOTAL.labels(provider=provider, reason=reason).inc()


def set_provider_circuit_state(*, provider: str, state: str) -> None:
    state_value = {"closed": 0, "half_open": 1, "open": 2}.get(state, 0)
    PROVIDER_CIRCUIT_STATE.labels(provider=provider).set(state_value)


def record_provider_circuit_transition(*, provider: str, state: str) -> None:
    PROVIDER_CIRCUIT_TRANSITIONS_TOTAL.labels(provider=provider, state=state).inc()


def set_process_close_wait_sockets(count: int) -> None:
    PROCESS_CLOSE_WAIT_SOCKETS.set(count)


def record_logo_public_read(*, result: str) -> None:
    TOKEN_LOGO_PUBLIC_TOTAL.labels(result=result).inc()


def record_logo_acquisition(*, outcome: str, source: str) -> None:
    TOKEN_LOGO_ACQUISITION_TOTAL.labels(outcome=outcome, source=source).inc()


def set_logo_due_count(count: int) -> None:
    TOKEN_LOGO_DUE.set(count)


def set_logo_acquisition_active(count: int) -> None:
    TOKEN_LOGO_ACQUISITION_ACTIVE.set(count)


def set_logo_source_refresh_age(*, source: str, chain_id: int, age_seconds: float) -> None:
    TOKEN_LOGO_SOURCE_REFRESH_AGE_SECONDS.labels(
        source=source,
        chain_id=str(chain_id),
    ).set(age_seconds)


def normalize_endpoint(endpoint: str) -> str:
    if endpoint.startswith("/token-logos/"):
        return "/token-logos/{chain_id}/{address}"
    return endpoint if endpoint in _KNOWN_ENDPOINTS else "/unknown"


def normalize_method(method: str) -> str:
    normalized = method.upper()
    return normalized if normalized in _KNOWN_METHODS else "OTHER"

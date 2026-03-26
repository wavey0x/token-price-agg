from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from token_price_agg.api.routes.health import router as health_router
from token_price_agg.api.routes.metrics import router as metrics_router
from token_price_agg.api.routes.prices import router as prices_router
from token_price_agg.api.routes.providers import router as providers_router
from token_price_agg.api.routes.quotes import router as quotes_router
from token_price_agg.api.routes.ready import router as ready_router
from token_price_agg.app.config import Settings, get_settings
from token_price_agg.app.dependencies import (
    get_anonymous_rate_limiter,
    get_api_key_store,
    get_provider_registry,
    get_token_metadata_resolver,
)
from token_price_agg.observability.logging import (
    RequestContextToken,
    bind_request_context,
    configure_logging,
    reset_request_context,
)
from token_price_agg.observability.metrics import (
    dec_inflight_request,
    inc_inflight_request,
    observe_http_request,
    record_auth_result,
    record_rate_limited,
)
from token_price_agg.security.models import AuthFailureReason, RateLimitResult

_REQUEST_LOGGER = logging.getLogger("token_price_agg.http")
_APP_LOGGER = logging.getLogger("token_price_agg.app")
_AUTH_FAILURE_MESSAGES = {
    AuthFailureReason.MISSING_AUTHORIZATION: "Missing API key — provide via Authorization: Bearer <key> or x-api-key header",
    AuthFailureReason.INVALID_AUTHORIZATION: "Invalid Authorization header",
    AuthFailureReason.REVOKED: "API key revoked",
    AuthFailureReason.EXPIRED: "API key expired",
}
_AUTH_STATUS_UNPROTECTED = "unprotected"
_AUTH_STATUS_AUTHENTICATED = "authenticated"
_AUTH_STATUS_ANONYMOUS = "anonymous"
_AUTH_STATUS_UNAUTHORIZED = "unauthorized"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        app_env=settings.app_env,
        app_version=settings.app_version,
    )
    try:
        resolver = get_token_metadata_resolver()
        await resolver.refresh_logo_sources()
    except Exception:
        _APP_LOGGER.exception("token_logo_source_startup_refresh_failed")
    yield
    registry = get_provider_registry()
    await registry.aclose()


app = FastAPI(
    title="Token Price Agg",
    version="0.1.0",
    lifespan=lifespan,
    swagger_ui_init_oauth={},
    openapi_tags=None,
)


def _custom_openapi() -> dict:  # type: ignore[type-arg]
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
        {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "Authorization: Bearer <api_key>",
            },
            "ApiKeyHeader": {
                "type": "apiKey",
                "in": "header",
                "name": "x-api-key",
                "description": "x-api-key: <api_key>",
            },
        }
    )
    schema["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]


@app.middleware("http")
async def request_observability_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    settings = get_settings()
    request_id, context_token = _init_request_context(request)

    started = time.perf_counter()
    status_code = 500

    if settings.metrics_enabled:
        inc_inflight_request()

    try:
        short_circuit, rate_limit_result = _authorize_and_rate_limit_if_needed(
            request=request,
            settings=settings,
            request_id=request_id,
        )
        if short_circuit is not None:
            status_code = short_circuit.status_code
            return short_circuit

        response = await call_next(request)
        status_code = response.status_code
        _apply_response_headers(
            response=response,
            request_id=request_id,
            rate_limit_result=rate_limit_result,
        )
        return response
    except Exception:
        _REQUEST_LOGGER.exception(
            "unhandled_exception",
            extra={
                "status_code": 500,
                "error_code": "UNHANDLED_EXCEPTION",
            },
        )
        raise
    finally:
        _finalize_observability(
            request=request,
            settings=settings,
            status_code=status_code,
            started=started,
            context_token=context_token,
        )


app.include_router(health_router)
app.include_router(ready_router)
app.include_router(providers_router)
app.include_router(metrics_router)
app.include_router(prices_router)
app.include_router(quotes_router)


def _init_request_context(request: Request) -> tuple[str, RequestContextToken]:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    _set_request_auth_state(request=request, auth_status=_AUTH_STATUS_UNPROTECTED)
    context_token = bind_request_context(
        request_id=request_id,
        path=request.url.path,
        method=request.method,
    )
    return request_id, context_token


def _authorize_and_rate_limit_if_needed(
    *,
    request: Request,
    settings: Settings,
    request_id: str,
) -> tuple[Response | None, RateLimitResult | None]:
    if not settings.api_key_auth_enabled or not request.url.path.startswith("/v1/"):
        _set_request_auth_state(request=request, auth_status=_AUTH_STATUS_UNPROTECTED)
        return None, None

    store = get_api_key_store()
    auth_result = store.authenticate_request_headers(
        request.headers.get("Authorization"),
        request.headers.get("x-api-key"),
    )
    if auth_result.authenticated:
        _set_request_auth_state(
            request=request,
            auth_status=_AUTH_STATUS_AUTHENTICATED,
            api_key_id=auth_result.public_id,
        )
        if settings.metrics_enabled:
            record_auth_result(result="ok")

        effective_limit_rpm = (
            auth_result.rate_limit_rpm
            if auth_result.rate_limit_rpm is not None
            else settings.api_key_rate_limit_rpm
        )
        rate_limit_result = store.consume_rate_limit(
            public_id=str(auth_result.public_id),
            limit_rpm=effective_limit_rpm,
        )
        if rate_limit_result.allowed:
            return None, rate_limit_result

        if settings.metrics_enabled:
            record_rate_limited(endpoint=request.url.path)
        return (
            _rate_limited_response(
                message="API key rate limit exceeded",
                rate_limit_result=rate_limit_result,
                request_id=request_id,
            ),
            None,
        )

    if _should_allow_unauthenticated_request(
        failure_reason=auth_result.failure_reason,
        settings=settings,
    ):
        _set_request_auth_state(
            request=request,
            auth_status=_AUTH_STATUS_ANONYMOUS,
            auth_reason=_auth_reason_value(auth_result.failure_reason),
        )
        if settings.metrics_enabled:
            record_auth_result(result="unauth_ok")

        limiter = get_anonymous_rate_limiter()
        rate_limit_result = limiter.consume(
            client_id=_anonymous_client_id(request),
            min_interval_seconds=settings.api_key_unauth_min_interval_seconds,
        )
        if rate_limit_result.allowed:
            return None, rate_limit_result

        if settings.metrics_enabled:
            record_auth_result(result="unauth_rate_limited")
            record_rate_limited(endpoint=request.url.path)
        return (
            _rate_limited_response(
                message="Anonymous rate limit exceeded",
                rate_limit_result=rate_limit_result,
                request_id=request_id,
            ),
            None,
        )

    _record_auth_failure_metrics(
        metrics_enabled=settings.metrics_enabled,
        failure_reason=auth_result.failure_reason,
    )
    _set_request_auth_state(
        request=request,
        auth_status=_AUTH_STATUS_UNAUTHORIZED,
        auth_reason=_auth_reason_value(auth_result.failure_reason),
    )
    unauthorized = _unauthorized_response(failure_reason=auth_result.failure_reason)
    unauthorized.headers["X-Request-ID"] = request_id
    return unauthorized, None


def _set_request_auth_state(
    *,
    request: Request,
    auth_status: str,
    auth_reason: str | None = None,
    api_key_id: str | None = None,
) -> None:
    request.state.auth_status = auth_status
    request.state.auth_reason = auth_reason
    request.state.api_key_id = api_key_id


def _auth_reason_value(failure_reason: AuthFailureReason | None) -> str | None:
    if failure_reason is None:
        return None
    return failure_reason.value


def _record_auth_failure_metrics(
    *,
    metrics_enabled: bool,
    failure_reason: AuthFailureReason | None,
) -> None:
    if not metrics_enabled:
        return
    result = (
        failure_reason.value
        if failure_reason is not None
        else AuthFailureReason.INVALID_KEY.value
    )
    record_auth_result(result=result)


def _should_allow_unauthenticated_request(
    *,
    failure_reason: AuthFailureReason | None,
    settings: Settings,
) -> bool:
    return (
        settings.api_key_unauth_access_enabled
        and failure_reason == AuthFailureReason.MISSING_AUTHORIZATION
    )


def _anonymous_client_id(request: Request) -> str:
    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def _apply_response_headers(
    *,
    response: Response,
    request_id: str,
    rate_limit_result: RateLimitResult | None,
) -> None:
    response.headers["X-Request-ID"] = request_id
    if rate_limit_result is None:
        return
    for key, value in rate_limit_result.headers().items():
        response.headers[key] = value


def _rate_limited_response(
    *,
    message: str,
    rate_limit_result: RateLimitResult,
    request_id: str,
) -> JSONResponse:
    limited = JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "RATE_LIMITED",
                "message": message,
            }
        },
    )
    for key, value in rate_limit_result.headers().items():
        limited.headers[key] = value
    limited.headers["X-Request-ID"] = request_id
    return limited


def _finalize_observability(
    *,
    request: Request,
    settings: Settings,
    status_code: int,
    started: float,
    context_token: RequestContextToken,
) -> None:
    elapsed_seconds = time.perf_counter() - started
    elapsed_ms = int(elapsed_seconds * 1000)

    if settings.metrics_enabled:
        observe_http_request(
            endpoint=request.url.path,
            method=request.method,
            status_code=status_code,
            duration_seconds=elapsed_seconds,
        )
        dec_inflight_request()

    request_log_extra: dict[str, int | str] = {
        "status_code": status_code,
        "latency_ms": elapsed_ms,
    }
    for field in ("auth_status", "auth_reason", "api_key_id"):
        value = getattr(request.state, field, None)
        if value is not None:
            request_log_extra[field] = value

    _REQUEST_LOGGER.info("http_request", extra=request_log_extra)
    reset_request_context(context_token)


def _unauthorized_response(
    *,
    failure_reason: AuthFailureReason | None,
) -> JSONResponse:
    if failure_reason is None:
        message = "Invalid API key"
    else:
        message = _AUTH_FAILURE_MESSAGES.get(failure_reason, "Invalid API key")

    response = JSONResponse(
        status_code=401,
        content={
            "detail": {
                "code": "UNAUTHORIZED",
                "message": message,
            }
        },
    )
    response.headers["WWW-Authenticate"] = "Bearer"
    return response

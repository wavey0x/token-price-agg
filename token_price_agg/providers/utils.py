from __future__ import annotations

from token_price_agg.core.errors import ErrorCode, ErrorInfo, ProviderStatus


def status_from_http_code(status_code: int) -> tuple[ProviderStatus, ErrorCode]:
    if status_code == 429:
        return ProviderStatus.ERROR, ErrorCode.RATE_LIMITED
    if status_code in {400, 422}:
        return ProviderStatus.BAD_REQUEST, ErrorCode.UPSTREAM_HTTP
    if status_code in {404}:
        return ProviderStatus.NO_ROUTE, ErrorCode.NO_ROUTE
    return ProviderStatus.ERROR, ErrorCode.UPSTREAM_HTTP


def make_error(
    code: ErrorCode, message: str, *, retry_after_ms: int | None = None
) -> ErrorInfo:
    return ErrorInfo(code=code, message=message, retry_after_ms=retry_after_ms)

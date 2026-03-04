from __future__ import annotations

from token_price_agg.core.errors import ErrorInfo, ProviderStatus


def status_from_http_code(status_code: int) -> ProviderStatus:
    if status_code == 429:
        return ProviderStatus.RATE_LIMITED
    if status_code in {400, 422}:
        return ProviderStatus.INVALID_REQUEST
    if status_code in {404}:
        return ProviderStatus.UNSUPPORTED_TOKEN
    if 500 <= status_code <= 599:
        return ProviderStatus.UPSTREAM_ERROR
    return ProviderStatus.UPSTREAM_ERROR


def error_from_status(status: ProviderStatus, detail: str) -> ErrorInfo:
    code_map = {
        ProviderStatus.UNSUPPORTED_TOKEN: "UNSUPPORTED_TOKEN",
        ProviderStatus.TIMEOUT: "TIMEOUT",
        ProviderStatus.UPSTREAM_ERROR: "UPSTREAM_ERROR",
        ProviderStatus.RATE_LIMITED: "RATE_LIMITED",
        ProviderStatus.INVALID_REQUEST: "INVALID_REQUEST",
        ProviderStatus.INTERNAL_ERROR: "INTERNAL_ERROR",
        ProviderStatus.STALE: "STALE",
        ProviderStatus.OK: "OK",
    }
    return ErrorInfo(code=code_map[status], message=detail)

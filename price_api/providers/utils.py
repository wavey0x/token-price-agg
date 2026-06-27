from __future__ import annotations

from price_api.core.errors import ErrorType, ProviderStatus


def status_from_http_code(status_code: int) -> tuple[ProviderStatus, ErrorType]:
    if status_code == 429:
        return ProviderStatus.ERROR, ErrorType.RATE_LIMITED
    if status_code in {400, 422}:
        return ProviderStatus.BAD_REQUEST, ErrorType.UPSTREAM_HTTP
    if status_code == 404:
        return ProviderStatus.NO_ROUTE, ErrorType.NO_ROUTE
    return ProviderStatus.ERROR, ErrorType.UPSTREAM_HTTP

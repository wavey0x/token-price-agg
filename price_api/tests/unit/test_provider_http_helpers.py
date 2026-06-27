from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from price_api.core.errors import ErrorType, ProviderStatus
from price_api.providers.clients.http import HttpResponse
from price_api.providers.http_helpers import non_200_status, parse_retry_after_ms


def test_non_200_status_sets_type_http_status_and_retry_hint() -> None:
    failure = non_200_status(
        response=_response(429, headers={"retry-after": "2"}),
        provider_name="Test",
    )

    assert failure is not None
    assert failure.status == ProviderStatus.ERROR
    assert failure.error_type == ErrorType.RATE_LIMITED
    assert failure.http_status_code == 429
    assert failure.retry_after_ms == 2000
    assert failure.to_error_info().model_dump(mode="json") == {
        "type": "RATE_LIMITED",
        "code": 429,
        "message": "Test returned 429",
        "retry_after_ms": 2000,
    }


def test_non_200_status_maps_upstream_http_status_without_retry_hint() -> None:
    failure = non_200_status(response=_response(500), provider_name="Test")

    assert failure is not None
    assert failure.status == ProviderStatus.ERROR
    assert failure.error_type == ErrorType.UPSTREAM_HTTP
    assert failure.to_error_info().model_dump(mode="json") == {
        "type": "UPSTREAM_HTTP",
        "code": 500,
        "message": "Test returned 500",
    }


def test_non_200_status_returns_none_for_success_status() -> None:
    assert non_200_status(response=_response(200), provider_name="Test") is None


def test_retry_after_parses_http_date() -> None:
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    retry_at = now + timedelta(seconds=3)

    assert (
        parse_retry_after_ms(
            _response(429, headers={"retry-after": format_datetime(retry_at)}),
            now=now,
        )
        == 3000
    )


def test_retry_after_ignores_invalid_negative_and_past_values() -> None:
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(seconds=1)

    assert parse_retry_after_ms(_response(429, headers={"retry-after": "nope"})) is None
    assert parse_retry_after_ms(_response(429, headers={"retry-after": "-1"})) is None
    assert (
        parse_retry_after_ms(
            _response(429, headers={"retry-after": format_datetime(past)}),
            now=now,
        )
        is None
    )


def _response(status_code: int, *, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        json_data={},
        text="",
        headers={key.lower(): value for key, value in (headers or {}).items()},
    )

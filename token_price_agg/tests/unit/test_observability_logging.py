from __future__ import annotations

import json
import logging

from token_price_agg.observability import logging as obs_logging


def test_request_context_bind_and_reset() -> None:
    token = obs_logging.bind_request_context(
        request_id="request-1",
        path="/v1/health",
        method="GET",
    )
    assert obs_logging.get_request_id() == "request-1"

    obs_logging.reset_request_context(token)
    assert obs_logging.get_request_id() is None


def test_redacts_sensitive_keys_and_bearer_values() -> None:
    payload = {
        "authorization": "Bearer super-secret",
        "x-api-key": "foo",
        "x-lifi-api-key": "abc",
        "nested": {
            "ENSO_API_KEY": "xyz",
            "normal": "ok",
        },
        "message": "contains api_key maybe",
    }

    redacted = obs_logging._redact(payload)
    assert redacted["authorization"] == "***REDACTED***"
    assert redacted["x-api-key"] == "***REDACTED***"
    assert redacted["x-lifi-api-key"] == "***REDACTED***"
    assert redacted["nested"]["ENSO_API_KEY"] == "***REDACTED***"
    assert redacted["nested"]["normal"] == "ok"
    assert redacted["message"] == "***REDACTED***"


def test_json_formatter_includes_auth_fields() -> None:
    formatter = obs_logging.JsonLogFormatter()
    token = obs_logging.bind_request_context(
        request_id="request-2",
        path="/v1/health",
        method="GET",
    )
    try:
        record = logging.LogRecord(
            name="token_price_agg.http",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="http_request",
            args=(),
            exc_info=None,
        )
        record.status_code = 429
        record.latency_ms = 0
        record.auth_status = "anonymous"
        record.auth_reason = "missing_authorization"

        payload = json.loads(formatter.format(record))
    finally:
        obs_logging.reset_request_context(token)

    assert payload["auth_status"] == "anonymous"
    assert payload["auth_reason"] == "missing_authorization"
    assert payload["status_code"] == 429

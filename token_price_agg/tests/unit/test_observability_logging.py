from __future__ import annotations

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

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_PATH: ContextVar[str | None] = ContextVar("request_path", default=None)
_REQUEST_METHOD: ContextVar[str | None] = ContextVar("request_method", default=None)

_LOG_ENV = "dev"
_LOG_VERSION = "0.0.0"

_SENSITIVE_KEYS = {
    "authorization",
    "x-api-key",
    "x-lifi-api-key",
    "lifi_api_key",
    "enso_api_key",
}


@dataclass(frozen=True)
class RequestContextToken:
    request_id: Token[str | None]
    path: Token[str | None]
    method: Token[str | None]


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _REQUEST_ID.get(),
            "path": _REQUEST_PATH.get(),
            "method": _REQUEST_METHOD.get(),
            "env": _LOG_ENV,
            "version": _LOG_VERSION,
        }

        for key in [
            "status_code",
            "latency_ms",
            "auth_status",
            "auth_reason",
            "provider",
            "provider_status",
            "error_code",
        ]:
            if hasattr(record, key):
                value = getattr(record, key)
                if value is not None:
                    payload[key] = value

        if record.exc_info is not None:
            exc_type = record.exc_info[0]
            exc_value = record.exc_info[1]
            payload["exc_type"] = exc_type.__name__ if exc_type is not None else "Exception"
            payload["exc_message"] = str(exc_value) if exc_value is not None else ""

        return json.dumps(_redact(payload), default=str, separators=(",", ":"))


def configure_logging(
    *,
    level: str,
    log_format: str,
    app_env: str,
    app_version: str,
) -> None:
    global _LOG_ENV
    global _LOG_VERSION

    _LOG_ENV = app_env
    _LOG_VERSION = app_version

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(stream=sys.stdout)
    if log_format.lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root.addHandler(handler)


def bind_request_context(*, request_id: str, path: str, method: str) -> RequestContextToken:
    return RequestContextToken(
        request_id=_REQUEST_ID.set(request_id),
        path=_REQUEST_PATH.set(path),
        method=_REQUEST_METHOD.set(method),
    )


def reset_request_context(token: RequestContextToken) -> None:
    _REQUEST_ID.reset(token.request_id)
    _REQUEST_PATH.reset(token.path)
    _REQUEST_METHOD.reset(token.method)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in _SENSITIVE_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, str):
        # Guard against accidental secret strings in messages.
        lowered = value.lower()
        if "bearer " in lowered:
            return "***REDACTED***"
        if "api_key" in lowered and len(value) > 6:
            return "***REDACTED***"
        return value
    return value

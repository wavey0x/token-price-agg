from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ProviderStatus(str, Enum):
    OK = "ok"
    NO_ROUTE = "no_route"
    ERROR = "error"
    BAD_REQUEST = "bad_request"


class ErrorCode(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_HTTP = "UPSTREAM_HTTP"
    UPSTREAM_PARSE = "UPSTREAM_PARSE"
    NO_ROUTE = "NO_ROUTE"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INTERNAL = "INTERNAL"
    INVALID_VAULT_CONVERSION = "INVALID_VAULT_CONVERSION"


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retry_after_ms: int | None = None


class AggregatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class InvalidRequestError(AggregatorError):
    pass


class UnsupportedOperationError(AggregatorError):
    pass

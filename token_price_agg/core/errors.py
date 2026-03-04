from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ProviderStatus(str, Enum):
    OK = "ok"
    UNSUPPORTED_TOKEN = "unsupported_token"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_ERROR = "internal_error"
    STALE = "stale"


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class AggregatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class InvalidRequestError(AggregatorError):
    pass


class UnsupportedOperationError(AggregatorError):
    pass

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_serializer


class ProviderStatus(str, Enum):
    OK = "ok"
    NO_ROUTE = "no_route"
    ERROR = "error"
    BAD_REQUEST = "bad_request"


class ErrorType(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_HTTP = "UPSTREAM_HTTP"
    UPSTREAM_PARSE = "UPSTREAM_PARSE"
    INTERNAL_TRANSPORT_TIMEOUT = "INTERNAL_TRANSPORT_TIMEOUT"
    NO_ROUTE = "NO_ROUTE"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    INTERNAL = "INTERNAL"
    VAULT_RESOLUTION_FAILED = "VAULT_RESOLUTION_FAILED"
    INVALID_VAULT_CONVERSION = "INVALID_VAULT_CONVERSION"


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ErrorType
    message: str
    code: int | None = None
    retry_after_ms: int | None = None

    @model_serializer
    def _serialize(self) -> dict[str, int | str]:
        data: dict[str, int | str] = {"type": self.type.value}
        if self.code is not None:
            data["code"] = self.code
        data["message"] = self.message
        if self.retry_after_ms is not None:
            data["retry_after_ms"] = self.retry_after_ms
        return data


class AggregatorError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        self.type = error_type
        self.message = message
        super().__init__(message)


class InvalidRequestError(AggregatorError):
    pass


class UnsupportedOperationError(AggregatorError):
    pass


class AdmissionRejectedError(AggregatorError):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        status_code: int,
        retry_after_seconds: int = 1,
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(error_type, message)

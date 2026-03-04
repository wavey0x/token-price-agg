from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AuthFailureReason(str, Enum):
    MISSING_AUTHORIZATION = "missing_authorization"
    INVALID_AUTHORIZATION = "invalid_authorization"
    INVALID_KEY = "invalid_key"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    public_id: str
    label: str
    key_prefix: str
    created_at: int
    last_used_at: int | None = None
    revoked_at: int | None = None
    revoked_reason: str | None = None
    expires_at: int | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyIssueResult:
    public_id: str
    label: str
    key: str
    key_prefix: str
    created_at: int


@dataclass(frozen=True, slots=True)
class AuthResult:
    authenticated: bool
    public_id: str | None = None
    label: str | None = None
    failure_reason: AuthFailureReason | None = None

    @classmethod
    def success(cls, *, public_id: str, label: str) -> AuthResult:
        return cls(authenticated=True, public_id=public_id, label=label)

    @classmethod
    def failure(cls, *, reason: AuthFailureReason) -> AuthResult:
        return cls(authenticated=False, failure_reason=reason)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int
    retry_after_seconds: int
    request_count: int

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_epoch),
            "Retry-After": str(self.retry_after_seconds),
        }


class InvalidateStatus(str, Enum):
    REVOKED = "revoked"
    ALREADY_REVOKED = "already_revoked"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class InvalidateResult:
    status: InvalidateStatus
    public_id: str
    revoked_at: int | None = None
    revoked_reason: str | None = None

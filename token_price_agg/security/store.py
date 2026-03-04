from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from token_price_agg.security.models import (
    ApiKeyIssueResult,
    ApiKeyRecord,
    AuthFailureReason,
    AuthResult,
    InvalidateResult,
    InvalidateStatus,
    RateLimitResult,
)

_KEY_PREFIX = "tpa_live_"
_PUBLIC_ID_PATTERN = re.compile(r"^[a-f0-9]{16}$")
_DEFAULT_RATE_WINDOW_SECONDS = 60
_RATE_LIMIT_RETENTION_WINDOWS = 120
_RATE_LIMIT_CLEANUP_INTERVAL_SECONDS = 30

_SQL_INSERT_API_KEY = """
INSERT INTO api_keys (
    public_id,
    label,
    secret_hash,
    key_prefix,
    created_at
) VALUES (?, ?, ?, ?, ?)
"""

_SQL_SELECT_AUTH_ROW = """
SELECT public_id, label, secret_hash, revoked_at, expires_at
FROM api_keys
WHERE public_id = ?
"""

_SQL_TOUCH_LAST_USED = "UPDATE api_keys SET last_used_at = ? WHERE public_id = ?"

_SQL_SELECT_KEYS_BASE = """
SELECT public_id, label, key_prefix, created_at, last_used_at, revoked_at,
       revoked_reason, expires_at
FROM api_keys
"""

_SQL_SELECT_INVALIDATE_ROW = "SELECT revoked_at, revoked_reason FROM api_keys WHERE public_id = ?"
_SQL_REVOKE_KEY = "UPDATE api_keys SET revoked_at = ?, revoked_reason = ? WHERE public_id = ?"

_SQL_UPSERT_RATE_WINDOW = """
INSERT INTO api_key_rate_windows (public_id, window_start, request_count)
VALUES (?, ?, 1)
ON CONFLICT(public_id, window_start)
DO UPDATE SET request_count = request_count + 1
RETURNING request_count
"""

_SQL_DELETE_OLD_RATE_WINDOWS = "DELETE FROM api_key_rate_windows WHERE window_start < ?"

_SQL_CREATE_API_KEYS_TABLE = """
CREATE TABLE IF NOT EXISTS api_keys (
    public_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    revoked_at INTEGER,
    revoked_reason TEXT,
    expires_at INTEGER
)
"""

_SQL_CREATE_RATE_WINDOWS_TABLE = """
CREATE TABLE IF NOT EXISTS api_key_rate_windows (
    public_id TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    request_count INTEGER NOT NULL,
    PRIMARY KEY(public_id, window_start),
    FOREIGN KEY(public_id) REFERENCES api_keys(public_id)
)
"""

_SQL_CREATE_RATE_WINDOWS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_api_key_rate_windows_window
ON api_key_rate_windows(window_start)
"""


@dataclass(frozen=True, slots=True)
class _IssuedKeyMaterial:
    public_id: str
    label: str
    raw_key: str
    key_prefix: str
    secret_hash: str
    created_at: int


class ApiKeyStore:
    def __init__(self, *, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._next_rate_window_cleanup_ts = 0
        self._ensure_db()

    def issue_key(self, *, label: str, now_ts: int | None = None) -> ApiKeyIssueResult:
        issued = _new_issued_key_material(label=label, now_ts=now_ts)

        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                _SQL_INSERT_API_KEY,
                (
                    issued.public_id,
                    issued.label,
                    issued.secret_hash,
                    issued.key_prefix,
                    issued.created_at,
                ),
            )
            conn.commit()

        return ApiKeyIssueResult(
            public_id=issued.public_id,
            label=issued.label,
            key=issued.raw_key,
            key_prefix=issued.key_prefix,
            created_at=issued.created_at,
        )

    def authenticate_bearer_header(
        self,
        authorization: str | None,
        *,
        now_ts: int | None = None,
    ) -> AuthResult:
        credential, failure_reason = _parse_bearer_credential(authorization)
        if credential is None:
            assert failure_reason is not None
            return AuthResult.failure(reason=failure_reason)
        return self.authenticate_key(credential, now_ts=now_ts)

    def authenticate_key(self, key: str, *, now_ts: int | None = None) -> AuthResult:
        parsed = _parse_key(key)
        if parsed is None:
            return AuthResult.failure(reason=AuthFailureReason.INVALID_KEY)
        public_id, secret = parsed

        now = _resolve_now(now_ts)

        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL_SELECT_AUTH_ROW, (public_id,)).fetchone()
            if row is None:
                return AuthResult.failure(reason=AuthFailureReason.INVALID_KEY)

            failure_reason = _auth_row_failure_reason(
                row=row,
                now=now,
                public_id=public_id,
                secret=secret,
            )
            if failure_reason is not None:
                return AuthResult.failure(reason=failure_reason)

            conn.execute(_SQL_TOUCH_LAST_USED, (now, public_id))
            conn.commit()
            label = str(row["label"])

        return AuthResult.success(public_id=public_id, label=label)

    def list_keys(self, *, include_revoked: bool = False) -> list[ApiKeyRecord]:
        query = _SQL_SELECT_KEYS_BASE
        if not include_revoked:
            query += " WHERE revoked_at IS NULL"
        query += " ORDER BY created_at DESC, public_id ASC"

        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query).fetchall()

        return [_api_key_record_from_row(row) for row in rows]

    def invalidate_key(
        self,
        *,
        public_id: str,
        reason: str | None = None,
        now_ts: int | None = None,
    ) -> InvalidateResult:
        now = _resolve_now(now_ts)
        normalized_reason = _normalize_reason(reason)

        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL_SELECT_INVALIDATE_ROW, (public_id,)).fetchone()
            if row is None:
                return InvalidateResult(status=InvalidateStatus.NOT_FOUND, public_id=public_id)

            revoked_at = _to_optional_int(row["revoked_at"])
            if revoked_at is not None:
                existing_reason = str(row["revoked_reason"]) if row["revoked_reason"] else None
                return InvalidateResult(
                    status=InvalidateStatus.ALREADY_REVOKED,
                    public_id=public_id,
                    revoked_at=revoked_at,
                    revoked_reason=existing_reason,
                )

            conn.execute(_SQL_REVOKE_KEY, (now, normalized_reason, public_id))
            conn.commit()

        return InvalidateResult(
            status=InvalidateStatus.REVOKED,
            public_id=public_id,
            revoked_at=now,
            revoked_reason=normalized_reason,
        )

    def consume_rate_limit(
        self,
        *,
        public_id: str,
        limit_rpm: int,
        now_ts: int | None = None,
    ) -> RateLimitResult:
        if limit_rpm <= 0:
            raise ValueError("limit_rpm must be > 0")

        now = _resolve_now(now_ts)
        window_start, reset_epoch, retry_after = _rate_window_for_now(now)

        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(_SQL_UPSERT_RATE_WINDOW, (public_id, window_start)).fetchone()
            self._next_rate_window_cleanup_ts = _cleanup_rate_windows_if_due(
                conn=conn,
                now=now,
                window_start=window_start,
                next_cleanup_ts=self._next_rate_window_cleanup_ts,
            )
            conn.commit()

        request_count = int(row[0]) if row is not None else 1
        allowed = request_count <= limit_rpm
        remaining = max(limit_rpm - request_count, 0)

        return RateLimitResult(
            allowed=allowed,
            limit=limit_rpm,
            remaining=remaining,
            reset_epoch=reset_epoch,
            retry_after_seconds=retry_after,
            request_count=request_count,
        )

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as conn:
            conn.execute(_SQL_CREATE_API_KEYS_TABLE)
            conn.execute(_SQL_CREATE_RATE_WINDOWS_TABLE)
            conn.execute(_SQL_CREATE_RATE_WINDOWS_INDEX)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn


def _new_issued_key_material(*, label: str, now_ts: int | None) -> _IssuedKeyMaterial:
    normalized_label = _normalize_label(label)
    created_at = _resolve_now(now_ts)
    public_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    raw_key = f"{_KEY_PREFIX}{public_id}.{secret}"
    key_prefix = f"{_KEY_PREFIX}{public_id}."
    secret_hash = _hash_secret(public_id=public_id, secret=secret)
    return _IssuedKeyMaterial(
        public_id=public_id,
        label=normalized_label,
        raw_key=raw_key,
        key_prefix=key_prefix,
        secret_hash=secret_hash,
        created_at=created_at,
    )


def _parse_bearer_credential(
    authorization: str | None,
) -> tuple[str | None, AuthFailureReason | None]:
    if not authorization:
        return None, AuthFailureReason.MISSING_AUTHORIZATION

    parts = authorization.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None, AuthFailureReason.INVALID_AUTHORIZATION
    scheme, credential = parts
    if scheme.lower() != "bearer":
        return None, AuthFailureReason.INVALID_AUTHORIZATION
    if not credential:
        return None, AuthFailureReason.INVALID_AUTHORIZATION

    return credential, None


def _auth_row_failure_reason(
    *,
    row: sqlite3.Row,
    now: int,
    public_id: str,
    secret: str,
) -> AuthFailureReason | None:
    revoked_at = _to_optional_int(row["revoked_at"])
    if revoked_at is not None:
        return AuthFailureReason.REVOKED

    expires_at = _to_optional_int(row["expires_at"])
    if expires_at is not None and now >= expires_at:
        return AuthFailureReason.EXPIRED

    expected_hash = str(row["secret_hash"])
    provided_hash = _hash_secret(public_id=public_id, secret=secret)
    if not hmac.compare_digest(expected_hash, provided_hash):
        return AuthFailureReason.INVALID_KEY

    return None


def _api_key_record_from_row(row: sqlite3.Row) -> ApiKeyRecord:
    return ApiKeyRecord(
        public_id=str(row["public_id"]),
        label=str(row["label"]),
        key_prefix=str(row["key_prefix"]),
        created_at=int(row["created_at"]),
        last_used_at=_to_optional_int(row["last_used_at"]),
        revoked_at=_to_optional_int(row["revoked_at"]),
        revoked_reason=str(row["revoked_reason"]) if row["revoked_reason"] else None,
        expires_at=_to_optional_int(row["expires_at"]),
    )


def _cleanup_rate_windows_if_due(
    *,
    conn: sqlite3.Connection,
    now: int,
    window_start: int,
    next_cleanup_ts: int,
) -> int:
    if now < next_cleanup_ts:
        return next_cleanup_ts
    cutoff = window_start - (_RATE_LIMIT_RETENTION_WINDOWS * _DEFAULT_RATE_WINDOW_SECONDS)
    conn.execute(_SQL_DELETE_OLD_RATE_WINDOWS, (cutoff,))
    return now + _RATE_LIMIT_CLEANUP_INTERVAL_SECONDS


def _rate_window_for_now(now: int) -> tuple[int, int, int]:
    window_start = now - (now % _DEFAULT_RATE_WINDOW_SECONDS)
    reset_epoch = window_start + _DEFAULT_RATE_WINDOW_SECONDS
    retry_after = max(reset_epoch - now, 0)
    return window_start, reset_epoch, retry_after


def _normalize_label(label: str) -> str:
    normalized_label = label.strip()
    if not normalized_label:
        raise ValueError("label must be non-empty")
    return normalized_label


def _normalize_reason(reason: str | None) -> str | None:
    if not isinstance(reason, str):
        return None
    stripped = reason.strip()
    return stripped or None


def _resolve_now(now_ts: int | None) -> int:
    return now_ts if now_ts is not None else int(time.time())


def _parse_key(key: str) -> tuple[str, str] | None:
    if not key.startswith(_KEY_PREFIX):
        return None
    raw = key[len(_KEY_PREFIX) :]
    if "." not in raw:
        return None
    public_id, secret = raw.split(".", 1)
    if not public_id or not secret:
        return None
    if _PUBLIC_ID_PATTERN.fullmatch(public_id) is None:
        return None
    return public_id, secret


def _hash_secret(*, public_id: str, secret: str) -> str:
    digest = hashlib.sha256()
    digest.update(public_id.encode("utf-8"))
    digest.update(b":")
    digest.update(secret.encode("utf-8"))
    return digest.hexdigest()


def _to_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"cannot convert value of type {type(value).__name__} to int")

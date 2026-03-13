from __future__ import annotations

from pathlib import Path

from token_price_agg.security.models import AuthFailureReason, DeleteStatus
from token_price_agg.security.store import ApiKeyStore


def test_api_key_store_lifecycle(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))

    issued = store.issue_key(label="alice", now_ts=1_700_000_000)
    assert issued.public_id
    assert issued.key.startswith(f"tpa_live_{issued.public_id}.")
    assert issued.key_prefix == f"tpa_live_{issued.public_id}."

    rows = store.list_keys()
    assert len(rows) == 1
    assert rows[0].public_id == issued.public_id
    assert rows[0].revoked_at is None

    auth_ok = store.authenticate_bearer_header(
        f"Bearer {issued.key}",
        now_ts=1_700_000_010,
    )
    assert auth_ok.authenticated is True
    assert auth_ok.public_id == issued.public_id
    assert auth_ok.label == "alice"

    refreshed_rows = store.list_keys()
    assert refreshed_rows[0].last_used_at == 1_700_000_010

    revoked = store.delete_key(
        public_id=issued.public_id,
        reason="manual revoke",
        now_ts=1_700_000_020,
    )
    assert revoked.status == DeleteStatus.DELETED
    assert revoked.revoked_at == 1_700_000_020
    assert revoked.revoked_reason == "manual revoke"

    auth_revoked = store.authenticate_key(issued.key, now_ts=1_700_000_021)
    assert auth_revoked.authenticated is False
    assert auth_revoked.failure_reason == AuthFailureReason.REVOKED

    active_rows = store.list_keys()
    assert active_rows == []

    all_rows = store.list_keys(include_revoked=True)
    assert len(all_rows) == 1
    assert all_rows[0].revoked_at == 1_700_000_020

    already = store.delete_key(public_id=issued.public_id, now_ts=1_700_000_022)
    assert already.status == DeleteStatus.ALREADY_DELETED

    missing = store.delete_key(public_id="deadbeefdeadbeef", now_ts=1_700_000_023)
    assert missing.status == DeleteStatus.NOT_FOUND


def test_api_key_auth_rejects_invalid_or_missing_authorization(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="bob", now_ts=1)

    missing = store.authenticate_bearer_header(None, now_ts=2)
    assert missing.authenticated is False
    assert missing.failure_reason == AuthFailureReason.MISSING_AUTHORIZATION

    malformed = store.authenticate_bearer_header("Bearer", now_ts=2)
    assert malformed.authenticated is False
    assert malformed.failure_reason == AuthFailureReason.INVALID_AUTHORIZATION

    wrong_scheme = store.authenticate_bearer_header(f"Token {issued.key}", now_ts=2)
    assert wrong_scheme.authenticated is False
    assert wrong_scheme.failure_reason == AuthFailureReason.INVALID_AUTHORIZATION

    wrong_secret = store.authenticate_key(f"tpa_live_{issued.public_id}.wrong", now_ts=2)
    assert wrong_secret.authenticated is False
    assert wrong_secret.failure_reason == AuthFailureReason.INVALID_KEY

    malformed_key = store.authenticate_key("not-a-key", now_ts=2)
    assert malformed_key.authenticated is False
    assert malformed_key.failure_reason == AuthFailureReason.INVALID_KEY


def test_authenticate_request_headers_bearer(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="eve", now_ts=1)

    result = store.authenticate_request_headers(
        f"Bearer {issued.key}", None, now_ts=2
    )
    assert result.authenticated is True
    assert result.public_id == issued.public_id


def test_authenticate_request_headers_x_api_key(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="frank", now_ts=1)

    result = store.authenticate_request_headers(None, issued.key, now_ts=2)
    assert result.authenticated is True
    assert result.public_id == issued.public_id


def test_authenticate_request_headers_bearer_takes_precedence(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="grace", now_ts=1)

    result = store.authenticate_request_headers(
        f"Bearer {issued.key}", "junk-key", now_ts=2
    )
    assert result.authenticated is True
    assert result.public_id == issued.public_id


def test_authenticate_request_headers_falls_back_to_x_api_key(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="heidi", now_ts=1)

    result = store.authenticate_request_headers(
        "Token not-bearer", issued.key, now_ts=2
    )
    assert result.authenticated is True
    assert result.public_id == issued.public_id


def test_authenticate_request_headers_missing_both(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))

    result = store.authenticate_request_headers(None, None, now_ts=2)
    assert result.authenticated is False
    assert result.failure_reason == AuthFailureReason.MISSING_AUTHORIZATION


def test_authenticate_request_headers_x_api_key_whitespace(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))

    result = store.authenticate_request_headers(None, "  ", now_ts=2)
    assert result.authenticated is False
    assert result.failure_reason == AuthFailureReason.MISSING_AUTHORIZATION


def test_fixed_window_rate_limit_rollover(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="carol", now_ts=1_700_000_000)
    key_id = issued.public_id

    first = store.consume_rate_limit(public_id=key_id, limit_rpm=3, now_ts=1_700_000_005)
    second = store.consume_rate_limit(public_id=key_id, limit_rpm=3, now_ts=1_700_000_006)
    third = store.consume_rate_limit(public_id=key_id, limit_rpm=3, now_ts=1_700_000_007)
    fourth = store.consume_rate_limit(public_id=key_id, limit_rpm=3, now_ts=1_700_000_008)

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is True
    assert fourth.allowed is False
    assert fourth.remaining == 0
    assert fourth.retry_after_seconds > 0
    assert fourth.headers()["X-RateLimit-Limit"] == "3"
    assert fourth.headers()["X-RateLimit-Remaining"] == "0"

    next_window = store.consume_rate_limit(public_id=key_id, limit_rpm=3, now_ts=1_700_000_061)
    assert next_window.allowed is True
    assert next_window.request_count == 1


def test_key_rate_limit_override_lifecycle(tmp_path: Path) -> None:
    store = ApiKeyStore(db_path=str(tmp_path / "api_keys.sqlite3"))
    issued = store.issue_key(label="dave", now_ts=1_700_000_000)

    updated = store.set_key_rate_limit(public_id=issued.public_id, rate_limit_rpm=12)
    assert updated is True

    auth = store.authenticate_key(issued.key, now_ts=1_700_000_001)
    assert auth.authenticated is True
    assert auth.rate_limit_rpm == 12

    rows = store.list_keys()
    assert rows[0].rate_limit_rpm == 12

    assert store.set_key_rate_limit(public_id="deadbeefdeadbeef", rate_limit_rpm=42) is False

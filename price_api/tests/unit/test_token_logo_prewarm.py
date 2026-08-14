from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from price_api.token_metadata.cache import TokenMetadataCache, read_logo_statuses
from price_api.tools import token_logo_prewarm

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def write_identities(path: Path) -> None:
    path.write_text(f"chain_id,address\n1,{USDC.lower()}\n", encoding="utf-8")


def test_identity_parser_deduplicates_and_rejects_urls(tmp_path: Path) -> None:
    identities = tmp_path / "identities.csv"
    identities.write_text(
        f"1,{USDC}\n1,{USDC.lower()}\n",
        encoding="utf-8",
    )
    assert token_logo_prewarm.read_identities(identities) == [(1, USDC)]

    identities.write_text("1,https://provider.example/logo.png\n", encoding="utf-8")
    with pytest.raises(Exception, match="Invalid EVM address"):
        token_logo_prewarm.read_identities(identities)


def test_enroll_requires_stopped_service_and_never_fetches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    TokenMetadataCache(db_path=str(db_path))
    identities = tmp_path / "identities.csv"
    write_identities(identities)
    monkeypatch.setattr(token_logo_prewarm, "_service_is_active", lambda _: False)

    token_logo_prewarm.main(
        [
            "--db-path",
            str(db_path),
            "enroll",
            "--input",
            str(identities),
            "--confirm-service-stopped",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity_count"] == 1
    assert isinstance(payload["enrollment_started_at_ms"], int)
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT last_attempt_at, next_attempt_at FROM token_logos").fetchone()
    assert row == (None, 0)


def test_enroll_refuses_active_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    TokenMetadataCache(db_path=str(db_path))
    identities = tmp_path / "identities.csv"
    write_identities(identities)
    monkeypatch.setattr(token_logo_prewarm, "_service_is_active", lambda _: True)

    with pytest.raises(SystemExit, match="is active"):
        token_logo_prewarm.main(
            [
                "--db-path",
                str(db_path),
                "enroll",
                "--input",
                str(identities),
                "--confirm-service-stopped",
            ]
        )


def test_read_only_status_reports_one_bounded_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    cache = TokenMetadataCache(db_path=str(db_path))
    identities = tmp_path / "identities.csv"
    write_identities(identities)
    cache.enroll_observed(chain_id=1, addresses=[USDC])
    cache.record_logo_failure(
        chain_id=1,
        address=USDC,
        outcome="unavailable",
        attempted_at=101,
        next_attempt_at=999,
        failure_count=0,
        http_status=404,
        error_code="http_404",
    )
    before = db_path.read_bytes()

    token_logo_prewarm.main(
        [
            "--db-path",
            str(db_path),
            "status",
            "--input",
            str(identities),
            "--started-at-ms",
            "100",
        ]
    )

    assert json.loads(capsys.readouterr().out) == {
        "attempted": 1,
        "command": "status",
        "identity_count": 1,
        "pending": 0,
        "success": 0,
        "transient": 0,
        "unavailable": 1,
    }
    assert db_path.read_bytes() == before
    assert read_logo_statuses(db_path=str(db_path), identities=[(1, USDC)])[0].last_outcome == (
        "unavailable"
    )


def test_wait_exits_nonzero_when_an_identity_was_never_attempted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    cache = TokenMetadataCache(db_path=str(db_path))
    cache.enroll_observed(chain_id=1, addresses=[USDC])
    identities = tmp_path / "identities.csv"
    write_identities(identities)

    with pytest.raises(SystemExit) as exc:
        token_logo_prewarm.main(
            [
                "--db-path",
                str(db_path),
                "wait",
                "--input",
                str(identities),
                "--started-at-ms",
                "1",
                "--deadline-seconds",
                "1",
                "--poll-seconds",
                "0.01",
            ]
        )
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["pending"] == 1


def test_attempt_at_enrollment_checkpoint_is_still_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    cache = TokenMetadataCache(db_path=str(db_path))
    cache.enroll_observed(chain_id=1, addresses=[USDC])
    cache.record_logo_failure(
        chain_id=1,
        address=USDC,
        outcome="unavailable",
        attempted_at=100,
        next_attempt_at=999,
        failure_count=0,
        http_status=404,
        error_code="http_404",
    )

    payload = token_logo_prewarm._status_payload(str(db_path), [(1, USDC)], 100)

    assert payload["pending"] == 1
    assert payload["attempted"] == 0


def test_terminal_success_is_complete_unless_force_existing_requeues_it(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    cache = TokenMetadataCache(db_path=str(db_path))
    body = b"owned-image"
    cache.record_logo_success(
        chain_id=1,
        address=USDC,
        image_bytes=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        mime_type="image/png",
        source="test",
        attempted_at=100,
        http_status=200,
    )

    checkpoint = cache.enroll_identities(identities=[(1, USDC)], force_existing=False)
    complete = token_logo_prewarm._status_payload(str(db_path), [(1, USDC)], checkpoint)
    assert complete["pending"] == 0
    assert complete["success"] == 1

    forced_checkpoint = cache.enroll_identities(
        identities=[(1, USDC)],
        force_existing=True,
    )
    forced = token_logo_prewarm._status_payload(
        str(db_path),
        [(1, USDC)],
        forced_checkpoint,
    )
    assert forced["pending"] == 1
    assert forced["attempted"] == 0

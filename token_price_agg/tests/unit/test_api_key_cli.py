from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

from token_price_agg.app.config import get_settings
from token_price_agg.tools import api_key


def test_api_key_cli_generate_list_invalidate_json(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setenv("API_KEY_DB_PATH", str(tmp_path / "api_keys.sqlite3"))
    get_settings.cache_clear()

    assert api_key.main(["generate", "--label", "ops", "--json"]) == 0
    generated = json.loads(capsys.readouterr().out)
    key_id = generated["id"]
    key = generated["key"]

    assert generated["label"] == "ops"
    assert key.startswith(f"tpa_live_{key_id}.")

    assert api_key.main(["list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1
    assert listed["keys"][0]["id"] == key_id

    assert api_key.main(["invalidate", key_id, "--reason", "rotate", "--json"]) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["status"] == "revoked"
    assert revoked["reason"] == "rotate"

    assert api_key.main(["list", "--json"]) == 0
    listed_active = json.loads(capsys.readouterr().out)
    assert listed_active["count"] == 0

    assert api_key.main(["list", "--all", "--json"]) == 0
    listed_all = json.loads(capsys.readouterr().out)
    assert listed_all["count"] == 1
    assert listed_all["keys"][0]["id"] == key_id
    assert listed_all["keys"][0]["revoked_at"] is not None


def test_api_key_cli_generate_interactive_label(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setenv("API_KEY_DB_PATH", str(tmp_path / "api_keys.sqlite3"))
    get_settings.cache_clear()
    monkeypatch.setattr("builtins.input", lambda _prompt: "operator")

    assert api_key.main(["generate", "--json"]) == 0
    generated = json.loads(capsys.readouterr().out)
    assert generated["label"] == "operator"


def test_api_key_cli_invalidate_unknown_is_non_destructive(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    monkeypatch.setenv("API_KEY_DB_PATH", str(tmp_path / "api_keys.sqlite3"))
    get_settings.cache_clear()

    assert api_key.main(["invalidate", "deadbeefdeadbeef", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not_found"

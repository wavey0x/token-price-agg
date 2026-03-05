from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from pytest import MonkeyPatch

from token_price_agg.app.config import get_settings
from token_price_agg.token_metadata.cache import TokenMetadataCache
from token_price_agg.token_metadata.logo_urls import LogoCandidate, build_logo_candidates
from token_price_agg.tools import verify_logo

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_build_logo_candidates_order_and_dedupe() -> None:
    candidates = build_logo_candidates(
        chain_id=1,
        address=USDC,
        provider_logo_url="https://example.com/logo.png",
        cached_logo_url="https://example.com/logo.png",
    )
    assert [item.source for item in candidates] == [
        "provider",
        "smoldapp",
        "yearn_tokenassets",
        "trustwallet",
    ]
    assert candidates[1].url == f"https://assets.smold.app/api/token/1/{USDC.lower()}/logo-128.png"
    assert (
        candidates[2].url
        == "https://raw.githubusercontent.com/yearn/tokenAssets/main/"
        f"tokens/1/{USDC.lower()}/logo-128.png"
    )


def test_cache_migration_adds_logo_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "token_cache.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_metadata (
            chain_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            symbol TEXT,
            decimals INTEGER,
            logo_url TEXT,
            source TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (chain_id, address)
        )
        """
    )
    conn.commit()
    conn.close()

    TokenMetadataCache(db_path=str(db_path))

    conn2 = sqlite3.connect(db_path)
    columns = {row[1] for row in conn2.execute("PRAGMA table_info(token_metadata)").fetchall()}
    conn2.close()
    assert "logo_status" in columns
    assert "logo_checked_at" in columns
    assert "logo_http_status" in columns


def test_verify_token_logo_marks_valid_and_persists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKEN_METADATA_DB_PATH", str(tmp_path / "token_cache.sqlite3"))
    get_settings.cache_clear()

    async def _fake_check(
        *,
        client: object,
        candidate: LogoCandidate,
        method: str,
    ) -> tuple[bool, int | None, str | None]:
        del client
        if candidate.source == "smoldapp" and method == "GET":
            return True, 200, None
        return False, 404, None

    monkeypatch.setattr(verify_logo, "_check_candidate", _fake_check)
    payload = asyncio.run(verify_logo.verify_token_logo(chain_id=1, token=USDC))

    assert payload["result"] == "valid"
    assert isinstance(payload["logo_url"], str)
    assert "assets.smold.app" in str(payload["logo_url"])

    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    row = cache.get_many(chain_id=1, addresses=[USDC])[USDC]
    assert row.logo_status == "valid"
    assert row.logo_url == payload["logo_url"]


def test_verify_token_logo_marks_invalid_and_persists(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKEN_METADATA_DB_PATH", str(tmp_path / "token_cache.sqlite3"))
    get_settings.cache_clear()

    async def _fake_check(
        *,
        client: object,
        candidate: LogoCandidate,
        method: str,
    ) -> tuple[bool, int | None, str | None]:
        del client, candidate, method
        return False, 404, None

    monkeypatch.setattr(verify_logo, "_check_candidate", _fake_check)
    payload = asyncio.run(verify_logo.verify_token_logo(chain_id=1, token=USDC))

    assert payload["result"] == "invalid"
    assert payload["logo_url"] is None
    assert payload["logo_http_status"] == 404

    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    row = cache.get_many(chain_id=1, addresses=[USDC])[USDC]
    assert row.logo_status == "invalid"
    assert row.logo_url is None

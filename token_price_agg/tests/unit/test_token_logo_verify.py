from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from pytest import MonkeyPatch

from token_price_agg.app.config import get_settings
from token_price_agg.token_metadata import logo_verifier
from token_price_agg.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache
from token_price_agg.token_metadata.logo_sources import TokenLogoSourceManager
from token_price_agg.token_metadata.logo_urls import LogoCandidate, build_logo_candidates
from token_price_agg.tools import verify_logo

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_build_logo_candidates_order_and_dedupe() -> None:
    candidates = build_logo_candidates(
        chain_id=1,
        address=USDC,
        provider_logo_urls=["https://example.com/logo.png"],
        cached_logo_url="https://example.com/logo.png",
    )
    # provider and cached are deduped; order is provider, yearn, trustwallet, smoldapp
    assert [item.source for item in candidates] == [
        "provider",
        "yearn_tokenassets",
        "trustwallet",
        "smoldapp",
    ]
    assert (
        candidates[1].url
        == "https://raw.githubusercontent.com/yearn/tokenAssets/main/"
        f"tokens/1/{USDC.lower()}/logo-128.png"
    )
    assert candidates[3].url == f"https://assets.smold.app/api/token/1/{USDC.lower()}/logo-128.png"


def test_build_logo_candidates_multiple_provider_urls() -> None:
    candidates = build_logo_candidates(
        chain_id=1,
        address=USDC,
        provider_logo_urls=[
            "https://provider-a.com/usdc.png",
            "https://provider-b.com/usdc.png",
        ],
    )
    assert candidates[0].url == "https://provider-a.com/usdc.png"
    assert candidates[0].source == "provider"
    assert candidates[1].url == "https://provider-b.com/usdc.png"
    assert candidates[1].source == "provider"


def test_build_logo_candidates_includes_source_candidates_before_static_fallbacks() -> None:
    candidates = build_logo_candidates(
        chain_id=1,
        address=USDC,
        additional_logo_candidates=[
            LogoCandidate(source="coingecko", url="https://assets.coingecko.com/usdc.png")
        ],
    )
    assert [item.source for item in candidates] == [
        "coingecko",
        "yearn_tokenassets",
        "trustwallet",
        "smoldapp",
    ]


def test_smoldapp_is_last_fallback() -> None:
    candidates = build_logo_candidates(
        chain_id=1,
        address=USDC,
    )
    assert candidates[-1].source == "smoldapp"


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
    assert "logo_source" in columns
    assert "logo_checked_at" in columns
    assert "logo_http_status" in columns


def test_cache_persists_logo_source_entries_and_sync_state(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    cache.replace_logo_source_entries(
        source="coingecko",
        chain_id=1,
        entries=[
            TokenLogoSourceEntry(
                source="coingecko",
                chain_id=1,
                address=USDC,
                logo_url="https://assets.coingecko.com/usdc.png",
            )
        ],
    )
    cache.upsert_logo_source_sync_state(source="coingecko", chain_id=1, synced_at=1234)

    entries = cache.get_logo_source_entries(chain_id=1, addresses=[USDC])
    assert entries[USDC][0].source == "coingecko"
    assert entries[USDC][0].logo_url == "https://assets.coingecko.com/usdc.png"

    state = cache.get_logo_source_sync_state(source="coingecko", chain_id=1)
    assert state is not None
    assert state.synced_at == 1234


def test_scrub_legacy_smoldapp_urls(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    from token_price_agg.core.models import TokenMetadata

    cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url=(
                    "https://raw.githubusercontent.com/SmolDapp/tokenAssets/main/"
                    "tokens/1/0x4e3fbd56cd56c3e72c1403e103b45db9da5b9d2b/logo-128.png"
                ),
                logo_status="valid",
                logo_checked_at=1_700_000_000,
                logo_http_status=200,
            )
        ]
    )

    count = cache.scrub_legacy_smoldapp_urls()
    assert count == 1

    row = cache.get_many(chain_id=1, addresses=[USDC])[USDC]
    assert row.logo_url is None
    assert row.logo_status == "unknown"
    assert row.logo_checked_at is None
    assert row.logo_http_status is None


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

    monkeypatch.setattr(logo_verifier, "_check_candidate", _fake_check)
    payload = asyncio.run(verify_logo.verify_token_logo(chain_id=1, token=USDC))

    assert payload["result"] == "valid"
    assert isinstance(payload["logo_url"], str)
    assert "assets.smold.app" in str(payload["logo_url"])
    assert payload["logo_source"] == "smoldapp"

    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    row = cache.get_many(chain_id=1, addresses=[USDC])[USDC]
    assert row.logo_status == "valid"
    assert row.logo_url == payload["logo_url"]
    assert row.logo_source == "smoldapp"


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

    monkeypatch.setattr(logo_verifier, "_check_candidate", _fake_check)
    payload = asyncio.run(verify_logo.verify_token_logo(chain_id=1, token=USDC))

    assert payload["result"] == "invalid"
    assert payload["logo_url"] is None
    assert payload["logo_http_status"] == 404

    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    row = cache.get_many(chain_id=1, addresses=[USDC])[USDC]
    assert row.logo_status == "invalid"
    assert row.logo_url is None


def test_verify_token_logo_uses_source_candidates(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOKEN_METADATA_DB_PATH", str(tmp_path / "token_cache.sqlite3"))
    get_settings.cache_clear()

    async def _fake_refresh(
        self: TokenLogoSourceManager,
        *,
        chain_id: int,
        force: bool = False,
    ) -> dict[str, int]:
        del self, chain_id, force
        return {}

    async def _fake_check(
        *,
        client: object,
        candidate: LogoCandidate,
        method: str,
    ) -> tuple[bool, int | None, str | None]:
        del client
        if candidate.source == "coingecko" and method == "GET":
            return True, 200, None
        return False, 404, None

    monkeypatch.setattr(TokenLogoSourceManager, "refresh_sources", _fake_refresh)
    monkeypatch.setattr(logo_verifier, "_check_candidate", _fake_check)

    cache = TokenMetadataCache(db_path=str(tmp_path / "token_cache.sqlite3"))
    cache.replace_logo_source_entries(
        source="coingecko",
        chain_id=1,
        entries=[
            TokenLogoSourceEntry(
                source="coingecko",
                chain_id=1,
                address=USDC,
                logo_url="https://assets.coingecko.com/usdc.png",
            )
        ],
    )

    payload = asyncio.run(verify_logo.verify_token_logo(chain_id=1, token=USDC))

    assert payload["result"] == "valid"
    assert payload["logo_source"] == "coingecko"
    assert payload["logo_url"] == "https://assets.coingecko.com/usdc.png"


def test_ssrf_protection_rejects_unsafe_urls() -> None:
    assert logo_verifier.is_safe_logo_url("https://example.com/logo.png") is True
    assert logo_verifier.is_safe_logo_url("http://example.com/logo.png") is False
    assert logo_verifier.is_safe_logo_url("https://localhost/logo.png") is False
    assert logo_verifier.is_safe_logo_url("https://127.0.0.1/logo.png") is False
    assert logo_verifier.is_safe_logo_url("https://10.0.0.1/logo.png") is False
    assert logo_verifier.is_safe_logo_url("https://192.168.1.1/logo.png") is False
    assert logo_verifier.is_safe_logo_url("https://172.16.0.1/logo.png") is False
    assert logo_verifier.is_safe_logo_url("ftp://example.com/logo.png") is False
    assert logo_verifier.is_safe_logo_url("") is False

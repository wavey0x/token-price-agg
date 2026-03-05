from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from token_price_agg.core.models import TokenMetadata


class TokenMetadataCache:
    def __init__(self, *, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._ensure_db()

    def get_many(self, *, chain_id: int, addresses: list[str]) -> dict[str, TokenMetadata]:
        if not addresses:
            return {}

        placeholders = ",".join("?" for _ in addresses)
        query = (
            "SELECT chain_id, address, symbol, decimals, logo_url, "
            "logo_status, logo_checked_at, logo_http_status, source "
            f"FROM token_metadata WHERE chain_id = ? AND address IN ({placeholders})"
        )
        params: list[object] = [chain_id, *addresses]

        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()

        out: dict[str, TokenMetadata] = {}
        for row in rows:
            metadata = TokenMetadata(
                chain_id=int(row["chain_id"]),
                address=str(row["address"]),
                symbol=str(row["symbol"]) if row["symbol"] is not None else None,
                decimals=int(row["decimals"]) if row["decimals"] is not None else None,
                logo_url=str(row["logo_url"]) if row["logo_url"] is not None else None,
                logo_status=(
                    str(row["logo_status"]) if row["logo_status"] is not None else "unknown"
                ),
                logo_checked_at=(
                    int(row["logo_checked_at"]) if row["logo_checked_at"] is not None else None
                ),
                logo_http_status=(
                    int(row["logo_http_status"]) if row["logo_http_status"] is not None else None
                ),
                source=str(row["source"]) if row["source"] is not None else None,
            )
            out[metadata.address] = metadata
        return out

    def upsert_many(self, items: list[TokenMetadata]) -> None:
        if not items:
            return

        now = int(time.time())
        rows = [
            (
                item.chain_id,
                item.address,
                item.symbol,
                item.decimals,
                item.logo_url,
                item.logo_status,
                item.logo_checked_at,
                item.logo_http_status,
                item.source,
                now,
            )
            for item in items
        ]

        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.executemany(
                """
                INSERT INTO token_metadata (
                    chain_id,
                    address,
                    symbol,
                    decimals,
                    logo_url,
                    logo_status,
                    logo_checked_at,
                    logo_http_status,
                    source,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, address) DO UPDATE SET
                    symbol = excluded.symbol,
                    decimals = excluded.decimals,
                    logo_url = excluded.logo_url,
                    logo_status = excluded.logo_status,
                    logo_checked_at = excluded.logo_checked_at,
                    logo_http_status = excluded.logo_http_status,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_metadata (
                    chain_id INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    symbol TEXT,
                    decimals INTEGER,
                    logo_url TEXT,
                    logo_status TEXT NOT NULL DEFAULT 'unknown',
                    logo_checked_at INTEGER,
                    logo_http_status INTEGER,
                    source TEXT,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (chain_id, address)
                )
                """
            )
            self._ensure_column(
                conn,
                column_name="logo_status",
                definition="TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._ensure_column(
                conn,
                column_name="logo_checked_at",
                definition="INTEGER",
            )
            self._ensure_column(
                conn,
                column_name="logo_http_status",
                definition="INTEGER",
            )
            conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, *, column_name: str, definition: str) -> None:
        rows = conn.execute("PRAGMA table_info(token_metadata)").fetchall()
        existing = {str(row[1]) for row in rows}
        if column_name in existing:
            return
        conn.execute(f"ALTER TABLE token_metadata ADD COLUMN {column_name} {definition}")

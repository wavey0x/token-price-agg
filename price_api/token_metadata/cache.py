from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from price_api.core.models import TokenMetadata
from price_api.core.validator import AddressValidator

TOKEN_METADATA_SCHEMA_VERSION = 2
_EXPECTED_TABLE_COLUMNS = {
    "token_metadata": (
        "chain_id",
        "address",
        "symbol",
        "decimals",
        "source",
        "updated_at",
    ),
    "token_logos": (
        "chain_id",
        "address",
        "image_bytes",
        "content_hash",
        "mime_type",
        "source",
        "last_success_at",
        "last_attempt_at",
        "next_attempt_at",
        "last_outcome",
        "failure_count",
        "last_http_status",
        "last_error_code",
    ),
    "token_logo_source_entries": (
        "source",
        "chain_id",
        "address",
        "logo_url",
        "updated_at",
    ),
    "token_logo_source_sync": (
        "source",
        "chain_id",
        "synced_at",
        "revision_hash",
    ),
}
_EXPECTED_INDEXES = {
    "idx_token_logos_due",
    "idx_token_logo_source_entries_lookup",
}


class IncompatibleTokenMetadataCache(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenLogoSourceEntry:
    source: str
    chain_id: int
    address: str
    logo_url: str


@dataclass(frozen=True)
class TokenLogoSourceSyncState:
    source: str
    chain_id: int
    synced_at: int
    revision_hash: str


@dataclass(frozen=True)
class TokenLogoAsset:
    image_bytes: bytes
    content_hash: str
    mime_type: str


@dataclass(frozen=True)
class DueTokenLogo:
    chain_id: int
    address: str
    failure_count: int


@dataclass(frozen=True)
class TokenLogoStatus:
    chain_id: int
    address: str
    has_image: bool
    last_attempt_at: int | None
    next_attempt_at: int | None
    last_outcome: str | None


class TokenMetadataCache:
    """Single-process SQLite store for metadata, logo acquisition state, and bytes."""

    def __init__(self, *, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._ensure_db()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get_many(self, *, chain_id: int, addresses: list[str]) -> dict[str, TokenMetadata]:
        normalized = _normalize_addresses(addresses)
        if not normalized:
            return {}

        placeholders = ",".join("?" for _ in normalized)
        query = (
            "SELECT chain_id, address, symbol, decimals, source "
            f"FROM token_metadata WHERE chain_id = ? AND address IN ({placeholders})"
        )
        params: list[object] = [chain_id, *normalized]

        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()

        out: dict[str, TokenMetadata] = {}
        for row in rows:
            metadata = TokenMetadata(
                chain_id=int(row["chain_id"]),
                address=str(row["address"]),
                symbol=str(row["symbol"]) if row["symbol"] is not None else None,
                decimals=int(row["decimals"]) if row["decimals"] is not None else None,
                logo_url=None,
                source=str(row["source"]) if row["source"] is not None else None,
            )
            out[metadata.address] = metadata
        return out

    def upsert_many(self, items: list[TokenMetadata]) -> None:
        if not items:
            return

        now = _now_ms()
        rows = [
            (
                item.chain_id,
                AddressValidator.normalize_address(item.address),
                item.symbol,
                item.decimals,
                item.source,
                now,
            )
            for item in items
        ]
        with self._lock, closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO token_metadata (
                    chain_id, address, symbol, decimals, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, address) DO UPDATE SET
                    symbol = excluded.symbol,
                    decimals = excluded.decimals,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()

    def enroll_observed(self, *, chain_id: int, addresses: list[str]) -> None:
        """Enroll only identities not already known; observations never force retries."""
        rows = [(chain_id, address) for address in _normalize_addresses(addresses)]
        if not rows:
            return
        with self._lock, closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO token_logos (chain_id, address, next_attempt_at)
                VALUES (?, ?, 0)
                ON CONFLICT(chain_id, address) DO NOTHING
                """,
                rows,
            )
            conn.commit()

    def enroll_identities(
        self,
        *,
        identities: list[tuple[int, str]],
        force_existing: bool,
    ) -> int:
        """Make an explicit identity set due and return its UTC-ms checkpoint."""
        normalized = sorted(
            {
                (chain_id, AddressValidator.normalize_address(address))
                for chain_id, address in identities
                if chain_id > 0
            }
        )
        if len(normalized) != len(identities):
            invalid_chain = next((chain_id for chain_id, _ in identities if chain_id <= 0), None)
            if invalid_chain is not None:
                raise ValueError("chain_id must be positive")

        started_at = _now_ms()
        if not normalized:
            return started_at

        conflict_sql = (
            "next_attempt_at = 0"
            if force_existing
            else (
                "next_attempt_at = CASE WHEN token_logos.image_bytes IS NULL "
                "THEN 0 ELSE token_logos.next_attempt_at END"
            )
        )
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                f"""
                INSERT INTO token_logos (chain_id, address, next_attempt_at)
                VALUES (?, ?, 0)
                ON CONFLICT(chain_id, address) DO UPDATE SET {conflict_sql}
                """,
                normalized,
            )
            conn.commit()
        return started_at

    def get_logo_asset(self, *, chain_id: int, address: str) -> TokenLogoAsset | None:
        normalized = AddressValidator.normalize_address(address)
        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT image_bytes, content_hash, mime_type
                FROM token_logos
                WHERE chain_id = ? AND address = ? AND image_bytes IS NOT NULL
                """,
                (chain_id, normalized),
            ).fetchone()
        if row is None:
            return None
        return TokenLogoAsset(
            image_bytes=bytes(row["image_bytes"]),
            content_hash=str(row["content_hash"]),
            mime_type=str(row["mime_type"]),
        )

    def get_due_logos(self, *, now_ms: int, limit: int) -> list[DueTokenLogo]:
        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT chain_id, address, failure_count
                FROM token_logos
                WHERE next_attempt_at IS NOT NULL AND next_attempt_at <= ?
                ORDER BY next_attempt_at, chain_id, address
                LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
        return [
            DueTokenLogo(
                chain_id=int(row["chain_id"]),
                address=str(row["address"]),
                failure_count=int(row["failure_count"]),
            )
            for row in rows
        ]

    def count_due_logos(self, *, now_ms: int) -> int:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT count(*) FROM token_logos
                WHERE next_attempt_at IS NOT NULL AND next_attempt_at <= ?
                """,
                (now_ms,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def record_logo_success(
        self,
        *,
        chain_id: int,
        address: str,
        image_bytes: bytes,
        content_hash: str,
        mime_type: str,
        source: str,
        attempted_at: int,
        http_status: int | None,
    ) -> None:
        normalized = AddressValidator.normalize_address(address)
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT content_hash FROM token_logos WHERE chain_id = ? AND address = ?",
                (chain_id, normalized),
            ).fetchone()
            if existing is not None and existing[0] == content_hash:
                conn.execute(
                    """
                    UPDATE token_logos
                    SET mime_type = ?, source = ?, last_success_at = ?, last_attempt_at = ?,
                        next_attempt_at = NULL, last_outcome = 'success', failure_count = 0,
                        last_http_status = ?, last_error_code = NULL
                    WHERE chain_id = ? AND address = ?
                    """,
                    (
                        mime_type,
                        source,
                        attempted_at,
                        attempted_at,
                        http_status,
                        chain_id,
                        normalized,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO token_logos (
                        chain_id, address, image_bytes, content_hash, mime_type, source,
                        last_success_at, last_attempt_at, next_attempt_at, last_outcome,
                        failure_count, last_http_status, last_error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'success', 0, ?, NULL)
                    ON CONFLICT(chain_id, address) DO UPDATE SET
                        image_bytes = excluded.image_bytes,
                        content_hash = excluded.content_hash,
                        mime_type = excluded.mime_type,
                        source = excluded.source,
                        last_success_at = excluded.last_success_at,
                        last_attempt_at = excluded.last_attempt_at,
                        next_attempt_at = NULL,
                        last_outcome = 'success',
                        failure_count = 0,
                        last_http_status = excluded.last_http_status,
                        last_error_code = NULL
                    """,
                    (
                        chain_id,
                        normalized,
                        sqlite3.Binary(image_bytes),
                        content_hash,
                        mime_type,
                        source,
                        attempted_at,
                        attempted_at,
                        http_status,
                    ),
                )
            conn.commit()

    def record_logo_failure(
        self,
        *,
        chain_id: int,
        address: str,
        outcome: str,
        attempted_at: int,
        next_attempt_at: int,
        failure_count: int,
        http_status: int | None,
        error_code: str,
    ) -> None:
        if outcome not in {"unavailable", "transient"}:
            raise ValueError("invalid logo acquisition failure outcome")
        normalized = AddressValidator.normalize_address(address)
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO token_logos (
                    chain_id, address, last_attempt_at, next_attempt_at, last_outcome,
                    failure_count, last_http_status, last_error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, address) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    next_attempt_at = excluded.next_attempt_at,
                    last_outcome = excluded.last_outcome,
                    failure_count = excluded.failure_count,
                    last_http_status = excluded.last_http_status,
                    last_error_code = excluded.last_error_code
                """,
                (
                    chain_id,
                    normalized,
                    attempted_at,
                    next_attempt_at,
                    outcome,
                    failure_count,
                    http_status,
                    error_code,
                ),
            )
            conn.commit()

    def get_logo_source_entries(
        self,
        *,
        chain_id: int,
        addresses: list[str],
    ) -> dict[str, list[TokenLogoSourceEntry]]:
        normalized = _normalize_addresses(addresses)
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        query = (
            "SELECT source, chain_id, address, logo_url FROM token_logo_source_entries "
            f"WHERE chain_id = ? AND address IN ({placeholders})"
        )
        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, [chain_id, *normalized]).fetchall()

        out: dict[str, list[TokenLogoSourceEntry]] = {}
        for row in rows:
            entry = TokenLogoSourceEntry(
                source=str(row["source"]),
                chain_id=int(row["chain_id"]),
                address=str(row["address"]),
                logo_url=str(row["logo_url"]),
            )
            out.setdefault(entry.address, []).append(entry)
        return out

    def replace_logo_source_entries(
        self,
        *,
        source: str,
        chain_id: int,
        entries: list[TokenLogoSourceEntry],
        synced_at: int,
    ) -> bool:
        normalized_rows = sorted(
            {
                (
                    AddressValidator.normalize_address(entry.address),
                    entry.logo_url,
                )
                for entry in entries
                if entry.source == source and entry.chain_id == chain_id
            }
        )
        revision_hash = hashlib.sha256(repr(normalized_rows).encode()).hexdigest()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                """
                SELECT revision_hash FROM token_logo_source_sync
                WHERE source = ? AND chain_id = ?
                """,
                (source, chain_id),
            ).fetchone()
            changed = previous is None or previous[0] != revision_hash
            if changed:
                conn.execute(
                    "DELETE FROM token_logo_source_entries WHERE source = ? AND chain_id = ?",
                    (source, chain_id),
                )
                conn.executemany(
                    """
                    INSERT INTO token_logo_source_entries (
                        source, chain_id, address, logo_url, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (source, chain_id, address, logo_url, synced_at)
                        for address, logo_url in normalized_rows
                    ],
                )
                conn.execute(
                    """
                    UPDATE token_logos SET next_attempt_at = 0
                    WHERE chain_id = ? AND image_bytes IS NULL
                    """,
                    (chain_id,),
                )
            conn.execute(
                """
                INSERT INTO token_logo_source_sync (
                    source, chain_id, synced_at, revision_hash
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source, chain_id) DO UPDATE SET
                    synced_at = excluded.synced_at,
                    revision_hash = excluded.revision_hash
                """,
                (source, chain_id, synced_at, revision_hash),
            )
            conn.commit()
        return changed

    def get_logo_source_sync_state(
        self,
        *,
        source: str,
        chain_id: int,
    ) -> TokenLogoSourceSyncState | None:
        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT source, chain_id, synced_at, revision_hash
                FROM token_logo_source_sync
                WHERE source = ? AND chain_id = ?
                """,
                (source, chain_id),
            ).fetchone()
        if row is None:
            return None
        return TokenLogoSourceSyncState(
            source=str(row["source"]),
            chain_id=int(row["chain_id"]),
            synced_at=int(row["synced_at"]),
            revision_hash=str(row["revision_hash"]),
        )

    def get_logo_statuses(
        self,
        *,
        identities: list[tuple[int, str]],
    ) -> list[TokenLogoStatus]:
        normalized = [
            (chain_id, AddressValidator.normalize_address(address))
            for chain_id, address in identities
        ]
        with self._lock, closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            statuses: list[TokenLogoStatus] = []
            for chain_id, address in normalized:
                row = conn.execute(
                    """
                    SELECT image_bytes IS NOT NULL AS has_image, last_attempt_at,
                           next_attempt_at, last_outcome
                    FROM token_logos WHERE chain_id = ? AND address = ?
                    """,
                    (chain_id, address),
                ).fetchone()
                statuses.append(
                    TokenLogoStatus(
                        chain_id=chain_id,
                        address=address,
                        has_image=bool(row["has_image"]) if row is not None else False,
                        last_attempt_at=(
                            int(row["last_attempt_at"])
                            if row is not None and row["last_attempt_at"] is not None
                            else None
                        ),
                        next_attempt_at=(
                            int(row["next_attempt_at"])
                            if row is not None and row["next_attempt_at"] is not None
                            else None
                        ),
                        last_outcome=(
                            str(row["last_outcome"])
                            if row is not None and row["last_outcome"] is not None
                            else None
                        ),
                    )
                )
        return statuses

    def quick_check(self) -> str:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row is not None else "missing"

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=5)

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as conn:
            existing_tables = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if existing_tables and schema_version != TOKEN_METADATA_SCHEMA_VERSION:
                raise IncompatibleTokenMetadataCache(
                    "token metadata cache schema is incompatible; stop the service, archive "
                    "the legacy cache, and create a clean schema before restart"
                )
            if not existing_tables:
                _create_schema(conn)
                conn.execute(f"PRAGMA user_version = {TOKEN_METADATA_SCHEMA_VERSION}")
                conn.commit()
            else:
                _validate_current_schema(conn, existing_tables=existing_tables)


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE token_metadata (
            chain_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            symbol TEXT,
            decimals INTEGER,
            source TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (chain_id, address)
        );

        CREATE TABLE token_logos (
            chain_id          INTEGER NOT NULL,
            address           TEXT NOT NULL,
            image_bytes       BLOB,
            content_hash      TEXT,
            mime_type         TEXT,
            source            TEXT,
            last_success_at   INTEGER,
            last_attempt_at   INTEGER,
            next_attempt_at   INTEGER DEFAULT 0,
            last_outcome      TEXT CHECK (
                last_outcome IS NULL OR
                last_outcome IN ('success', 'unavailable', 'transient')
            ),
            failure_count     INTEGER NOT NULL DEFAULT 0,
            last_http_status  INTEGER,
            last_error_code   TEXT,
            PRIMARY KEY (chain_id, address),
            CHECK (
                (
                    image_bytes IS NULL AND content_hash IS NULL AND mime_type IS NULL
                    AND source IS NULL AND last_success_at IS NULL
                ) OR (
                    image_bytes IS NOT NULL AND content_hash IS NOT NULL AND mime_type IS NOT NULL
                    AND source IS NOT NULL AND last_success_at IS NOT NULL
                )
            ),
            CHECK (image_bytes IS NULL OR length(image_bytes) BETWEEN 1 AND 262144),
            CHECK (content_hash IS NULL OR length(content_hash) = 64),
            CHECK (mime_type IS NULL OR mime_type IN ('image/png', 'image/jpeg', 'image/webp')),
            CHECK (source IS NULL OR length(source) BETWEEN 1 AND 64),
            CHECK (failure_count >= 0),
            CHECK (last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599),
            CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 64)
        );

        CREATE INDEX idx_token_logos_due
        ON token_logos (next_attempt_at, chain_id, address);

        CREATE TABLE token_logo_source_entries (
            source TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            logo_url TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (source, chain_id, address)
        );

        CREATE INDEX idx_token_logo_source_entries_lookup
        ON token_logo_source_entries (chain_id, address);

        CREATE TABLE token_logo_source_sync (
            source TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            synced_at INTEGER NOT NULL,
            revision_hash TEXT NOT NULL CHECK (length(revision_hash) = 64),
            PRIMARY KEY (source, chain_id)
        );
        """
    )


def _validate_current_schema(
    conn: sqlite3.Connection,
    *,
    existing_tables: set[str],
) -> None:
    if existing_tables != set(_EXPECTED_TABLE_COLUMNS):
        raise IncompatibleTokenMetadataCache(
            "token metadata cache does not match the clean token-logo schema"
        )
    for table, expected_columns in _EXPECTED_TABLE_COLUMNS.items():
        actual_columns = tuple(
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        )
        if actual_columns != expected_columns:
            raise IncompatibleTokenMetadataCache(
                f"token metadata cache table {table} has incompatible columns"
            )
    actual_indexes = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if not _EXPECTED_INDEXES.issubset(actual_indexes):
        raise IncompatibleTokenMetadataCache(
            "token metadata cache is missing required indexes"
        )


def _normalize_addresses(addresses: list[str]) -> list[str]:
    return list(dict.fromkeys(AddressValidator.normalize_address(address) for address in addresses))


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def read_logo_statuses(
    *,
    db_path: str,
    identities: list[tuple[int, str]],
) -> list[TokenLogoStatus]:
    """Read prewarm state through a query-only connection without initializing SQLite."""
    path = Path(db_path).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.execute("PRAGMA query_only = ON")
        schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if schema_version != TOKEN_METADATA_SCHEMA_VERSION:
            raise IncompatibleTokenMetadataCache("token metadata cache schema is incompatible")
        existing_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        _validate_current_schema(conn, existing_tables=existing_tables)
        conn.row_factory = sqlite3.Row
        statuses: list[TokenLogoStatus] = []
        for chain_id, raw_address in identities:
            address = AddressValidator.normalize_address(raw_address)
            row = conn.execute(
                """
                SELECT image_bytes IS NOT NULL AS has_image, last_attempt_at,
                       next_attempt_at, last_outcome
                FROM token_logos WHERE chain_id = ? AND address = ?
                """,
                (chain_id, address),
            ).fetchone()
            statuses.append(
                TokenLogoStatus(
                    chain_id=chain_id,
                    address=address,
                    has_image=bool(row["has_image"]) if row is not None else False,
                    last_attempt_at=(
                        int(row["last_attempt_at"])
                        if row is not None and row["last_attempt_at"] is not None
                        else None
                    ),
                    next_attempt_at=(
                        int(row["next_attempt_at"])
                        if row is not None and row["next_attempt_at"] is not None
                        else None
                    ),
                    last_outcome=(
                        str(row["last_outcome"])
                        if row is not None and row["last_outcome"] is not None
                        else None
                    ),
                )
            )
    return statuses

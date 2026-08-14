from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from price_api.token_metadata.cache import (
    IncompatibleTokenMetadataCache,
    TokenLogoSourceEntry,
    TokenMetadataCache,
)
from price_api.token_metadata.logo_acquirer import (
    MAX_IMAGE_BYTES,
    LogoValidationError,
    TokenLogoAcquirer,
    validate_logo_bytes,
)
from price_api.token_metadata.logo_sources import TokenLogoSourceManager

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WFRAX = "0x853d955aCEf822Db058eb8505911ED77F175b99e"


@dataclass
class FixedSource:
    id: str
    url: str
    metadata_url: str | None = None

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id == 1

    def deterministic_candidate(self, *, chain_id: int, address: str) -> str | None:
        del address
        return self.url if self.supports_chain(chain_id) else None

    def parse_metadata(self, *, chain_id: int, payload: object) -> list[TokenLogoSourceEntry]:
        del chain_id, payload
        return []

    def allows_image_url(self, *, chain_id: int, address: str, url: str) -> bool:
        del address
        return chain_id == 1 and url == self.url


def image_bytes(
    *,
    image_format: str = "PNG",
    size: tuple[int, int] = (32, 32),
) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, (12, 34, 56, 255)).save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_complete_raster_validation_preserves_original_bytes(
    image_format: str,
    mime_type: str,
) -> None:
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    output = BytesIO()
    Image.new(mode, (32, 32), (12, 34, 56)).save(output, format=image_format)
    body = output.getvalue()

    result = validate_logo_bytes(body, mime_type=mime_type)

    assert result.image_bytes == body
    assert len(result.content_hash) == 64
    assert result.mime_type == mime_type


@pytest.mark.parametrize(
    ("body", "mime_type", "code"),
    [
        (b"", "image/png", "empty_body"),
        (b"not an image", "image/png", "signature_mismatch"),
        (image_bytes()[:-8], "image/png", "corrupt_image"),
        (image_bytes(), "image/jpeg", "signature_mismatch"),
        (image_bytes(), "image/gif", "unsupported_media"),
    ],
)
def test_invalid_or_mismatched_bodies_are_rejected(
    body: bytes,
    mime_type: str,
    code: str,
) -> None:
    with pytest.raises(LogoValidationError, match=code):
        validate_logo_bytes(body, mime_type=mime_type)


def test_dimension_and_stream_size_bounds_are_enforced() -> None:
    oversized_dimensions = image_bytes(size=(1025, 1))
    with pytest.raises(LogoValidationError, match="dimensions_too_large"):
        validate_logo_bytes(oversized_dimensions, mime_type="image/png")
    with pytest.raises(LogoValidationError, match="body_too_large"):
        validate_logo_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES, mime_type="image/png")


def test_animated_webp_is_rejected() -> None:
    output = BytesIO()
    frames = [
        Image.new("RGBA", (16, 16), (255, 0, 0, 255)),
        Image.new("RGBA", (16, 16), (0, 255, 0, 255)),
    ]
    try:
        frames[0].save(
            output,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
    except OSError:
        pytest.skip("Pillow build lacks animated WebP support")
    with pytest.raises(LogoValidationError, match="multiple_frames"):
        validate_logo_bytes(output.getvalue(), mime_type="image/webp")


@pytest.mark.asyncio
async def test_override_bytes_precede_all_remote_sources(tmp_path: Path) -> None:
    source = FixedSource("remote", "https://logos.example/remote.png")

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("WFRAX override should prevent a remote request")

    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(source,))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = TokenLogoAcquirer(source_manager=manager, client=client)
    try:
        result = await acquirer.acquire(chain_id=1, address=WFRAX)
    finally:
        await client.aclose()

    assert result.outcome == "success"
    assert result.source == "override"
    assert result.asset is not None


@pytest.mark.asyncio
async def test_candidate_aggregation_prefers_success_after_transient(tmp_path: Path) -> None:
    first = FixedSource("first", "https://logos.example/timeout.png")
    second = FixedSource("second", "https://logos.example/logo.png")
    body = image_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("timeout.png"):
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=body)

    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(first, second))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = TokenLogoAcquirer(source_manager=manager, client=client)
    try:
        result = await acquirer.acquire(chain_id=1, address=USDC)
    finally:
        await client.aclose()

    assert result.outcome == "success"
    assert result.source == "second"
    assert result.asset is not None and result.asset.image_bytes == body


@pytest.mark.asyncio
async def test_network_failure_uses_application_owned_error_code(tmp_path: Path) -> None:
    source = FixedSource("remote", "https://logos.example/timeout.png")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(source,))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = TokenLogoAcquirer(source_manager=manager, client=client)
    try:
        result = await acquirer.acquire(chain_id=1, address=USDC)
    finally:
        await client.aclose()

    assert result.outcome == "transient"
    assert result.error_code == "network_timeout"


@pytest.mark.asyncio
async def test_any_transient_takes_precedence_over_conclusive_failures(tmp_path: Path) -> None:
    first = FixedSource("first", "https://logos.example/503.png")
    second = FixedSource("second", "https://logos.example/invalid.png")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("503.png"):
            return httpx.Response(503, headers={"Retry-After": "999999"})
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"invalid")

    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(first, second))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = TokenLogoAcquirer(source_manager=manager, client=client)
    try:
        result = await acquirer.acquire(chain_id=1, address=USDC)
    finally:
        await client.aclose()

    assert result.outcome == "transient"
    assert result.http_status == 503
    assert result.retry_after_ms == 999_999_000


@pytest.mark.asyncio
async def test_redirect_and_oversized_stream_are_conclusive(tmp_path: Path) -> None:
    redirect = FixedSource("redirect", "https://logos.example/redirect.png")
    oversized = FixedSource("oversized", "https://logos.example/oversized.png")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("redirect.png"):
            return httpx.Response(302, headers={"Location": "https://other.example/logo.png"})
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=b"x" * (MAX_IMAGE_BYTES + 1),
        )

    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    manager = TokenLogoSourceManager(cache=cache, sources=(redirect, oversized))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    acquirer = TokenLogoAcquirer(source_manager=manager, client=client)
    try:
        result = await acquirer.acquire(chain_id=1, address=USDC)
    finally:
        await client.aclose()

    assert result.outcome == "unavailable"
    assert result.error_code == "body_too_large"


def test_clean_schema_and_due_index(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    TokenMetadataCache(db_path=str(db_path))
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        metadata_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(token_metadata)").fetchall()
        }
        assert metadata_columns == {
            "chain_id",
            "address",
            "symbol",
            "decimals",
            "source",
            "updated_at",
        }
        logo_columns = {row[1] for row in conn.execute("PRAGMA table_info(token_logos)").fetchall()}
        assert {"image_bytes", "content_hash", "mime_type", "next_attempt_at"}.issubset(
            logo_columns
        )
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN SELECT chain_id, address FROM token_logos
            WHERE next_attempt_at IS NOT NULL AND next_attempt_at <= ?
            ORDER BY next_attempt_at, chain_id, address LIMIT ?
            """,
            (0, 32),
        ).fetchall()
        assert any("idx_token_logos_due" in str(row) for row in plan)


def test_legacy_cache_schema_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE token_metadata (
                chain_id INTEGER, address TEXT, logo_url TEXT, logo_status TEXT
            )
            """
        )
    with pytest.raises(IncompatibleTokenMetadataCache, match="archive the legacy cache"):
        TokenMetadataCache(db_path=str(db_path))


def test_versioned_cache_with_legacy_columns_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "incompatible.sqlite3"
    TokenMetadataCache(db_path=str(db_path))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("ALTER TABLE token_metadata ADD COLUMN logo_url TEXT")
        conn.commit()

    with pytest.raises(IncompatibleTokenMetadataCache, match="incompatible columns"):
        TokenMetadataCache(db_path=str(db_path))


def test_atomic_logo_constraints_and_last_known_good_preservation(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    cache = TokenMetadataCache(db_path=str(db_path))
    body = image_bytes()
    asset = validate_logo_bytes(body, mime_type="image/png")
    cache.record_logo_success(
        chain_id=1,
        address=USDC,
        image_bytes=asset.image_bytes,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        source="test",
        attempted_at=100,
        http_status=200,
    )
    cache.record_logo_failure(
        chain_id=1,
        address=USDC,
        outcome="transient",
        attempted_at=200,
        next_attempt_at=300,
        failure_count=1,
        http_status=503,
        error_code="http_503",
    )

    retained = cache.get_logo_asset(chain_id=1, address=USDC.lower())
    assert retained is not None
    assert retained.image_bytes == body
    assert retained.content_hash == asset.content_hash

    with closing(sqlite3.connect(db_path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO token_logos (chain_id, address, image_bytes)
                VALUES (1, ?, ?)
                """,
                ("0x0000000000000000000000000000000000000001", body),
            )


def test_success_is_terminal_until_explicit_force_enrollment(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    body = image_bytes()
    asset = validate_logo_bytes(body, mime_type="image/png")
    cache.record_logo_success(
        chain_id=1,
        address=USDC,
        image_bytes=body,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        source="test",
        attempted_at=100,
        http_status=200,
    )
    assert cache.get_due_logos(now_ms=10**12, limit=32) == []

    cache.enroll_observed(chain_id=1, addresses=[USDC.lower()])
    assert cache.get_due_logos(now_ms=10**12, limit=32) == []

    cache.enroll_identities(identities=[(1, USDC)], force_existing=True)
    assert len(cache.get_due_logos(now_ms=10**12, limit=32)) == 1

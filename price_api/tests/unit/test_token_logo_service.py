from __future__ import annotations

import asyncio
import random
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from price_api.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache
from price_api.token_metadata.logo_acquirer import (
    AcquisitionResult,
    TokenLogoAcquirer,
    validate_logo_bytes,
)
from price_api.token_metadata.logo_overrides import get_logo_override
from price_api.token_metadata.logo_service import (
    LogoMaintenanceSettings,
    TokenLogoService,
    transient_retry_delay_ms,
    unavailable_retry_delay_ms,
)
from price_api.token_metadata.logo_sources import TokenLogoSourceManager

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WFRAX = "0x853d955aCEf822Db058eb8505911ED77F175b99e"


class FakeAcquirer:
    def __init__(self, result: AcquisitionResult, *, pause: float = 0) -> None:
        self.result = result
        self.pause = pause
        self.active = 0
        self.max_active = 0
        self.closed = False

    async def acquire(self, *, chain_id: int, address: str) -> AcquisitionResult:
        del chain_id, address
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.pause:
                await asyncio.sleep(self.pause)
            return self.result
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        self.closed = True


def test_retry_defaults_are_pinned_and_bounded() -> None:
    settings = LogoMaintenanceSettings()
    assert settings.source_refresh_ms == 12 * 60 * 60 * 1000
    assert settings.unavailable_retry_ms == 48 * 60 * 60 * 1000
    assert settings.transient_base_ms == 5 * 60 * 1000
    assert settings.transient_cap_ms == 6 * 60 * 60 * 1000
    assert settings.retry_after_cap_ms == 24 * 60 * 60 * 1000
    assert (settings.due_batch_size, settings.concurrency) == (32, 8)
    assert settings.scheduler_poll_ms == 60 * 1000
    assert settings.prewarm_deadline_ms == 30 * 60 * 1000
    assert settings.shutdown_grace_ms == 10 * 1000

    rng = random.Random(1)
    assert (
        transient_retry_delay_ms(
            failure_count=100,
            retry_after_ms=10**12,
            settings=settings,
            rng=rng,
        )
        == settings.retry_after_cap_ms
    )
    unavailable = unavailable_retry_delay_ms(settings=settings, rng=random.Random(2))
    assert (
        int(settings.unavailable_retry_ms * 0.9)
        <= unavailable
        <= int(settings.unavailable_retry_ms * 1.1)
    )


@pytest.mark.asyncio
async def test_service_commits_success_atomically_and_terminally(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    cache.enroll_observed(chain_id=1, addresses=[WFRAX])
    override = get_logo_override(chain_id=1, address=WFRAX)
    assert override is not None
    asset = validate_logo_bytes(override.image_bytes, mime_type="image/png")
    fake = FakeAcquirer(
        AcquisitionResult(
            outcome="success",
            asset=asset,
            source="override",
            http_status=None,
            error_code="success",
        )
    )
    manager = TokenLogoSourceManager(cache=cache, sources=())
    service = TokenLogoService(
        cache=cache,
        source_manager=manager,
        acquirer=cast(TokenLogoAcquirer, fake),
    )
    service._chain_ids = [1]
    await service.run_once()
    await service.aclose()

    stored = cache.get_logo_asset(chain_id=1, address=WFRAX)
    assert stored is not None and stored.image_bytes == asset.image_bytes
    assert cache.get_due_logos(now_ms=10**15, limit=32) == []
    assert fake.closed


@pytest.mark.asyncio
async def test_service_retains_last_known_good_after_failure(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    override = get_logo_override(chain_id=1, address=WFRAX)
    assert override is not None
    asset = validate_logo_bytes(override.image_bytes, mime_type="image/png")
    cache.record_logo_success(
        chain_id=1,
        address=WFRAX,
        image_bytes=asset.image_bytes,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        source="override",
        attempted_at=1,
        http_status=None,
    )
    cache.enroll_identities(identities=[(1, WFRAX)], force_existing=True)
    fake = FakeAcquirer(
        AcquisitionResult(
            outcome="transient",
            asset=None,
            source=None,
            http_status=503,
            error_code="http_503",
        )
    )
    manager = TokenLogoSourceManager(cache=cache, sources=())
    service = TokenLogoService(
        cache=cache,
        source_manager=manager,
        acquirer=cast(TokenLogoAcquirer, fake),
        rng=random.Random(3),
    )
    service._chain_ids = [1]
    await service.run_once()
    await service.aclose()

    retained = cache.get_logo_asset(chain_id=1, address=WFRAX)
    assert retained is not None and retained.content_hash == asset.content_hash
    with closing(sqlite3.connect(cache.db_path)) as conn:
        outcome, failure_count = conn.execute(
            "SELECT last_outcome, failure_count FROM token_logos WHERE chain_id=1 AND address=?",
            (WFRAX,),
        ).fetchone()
    assert (outcome, failure_count) == ("transient", 1)


@pytest.mark.asyncio
async def test_due_batch_obeys_concurrency_limit(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    identities = [(1, f"0x{number:040x}") for number in range(1, 13)]
    cache.enroll_identities(identities=identities, force_existing=False)
    fake = FakeAcquirer(
        AcquisitionResult(
            outcome="unavailable",
            asset=None,
            source=None,
            http_status=404,
            error_code="http_404",
        ),
        pause=0.01,
    )
    manager = TokenLogoSourceManager(cache=cache, sources=())
    settings = LogoMaintenanceSettings(concurrency=3)
    service = TokenLogoService(
        cache=cache,
        source_manager=manager,
        acquirer=cast(TokenLogoAcquirer, fake),
        settings=settings,
    )
    service._chain_ids = [1]
    await service.run_once()
    await service.aclose()

    assert fake.max_active == 3


def test_changed_source_list_redrives_only_rows_without_bytes(tmp_path: Path) -> None:
    cache = TokenMetadataCache(db_path=str(tmp_path / "metadata.sqlite3"))
    cache.enroll_identities(identities=[(1, USDC), (1, WFRAX)], force_existing=False)
    cache.record_logo_failure(
        chain_id=1,
        address=USDC,
        outcome="unavailable",
        attempted_at=1,
        next_attempt_at=10**15,
        failure_count=0,
        http_status=404,
        error_code="http_404",
    )
    override = get_logo_override(chain_id=1, address=WFRAX)
    assert override is not None
    asset = validate_logo_bytes(override.image_bytes, mime_type="image/png")
    cache.record_logo_success(
        chain_id=1,
        address=WFRAX,
        image_bytes=asset.image_bytes,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        source="override",
        attempted_at=1,
        http_status=None,
    )

    cache.replace_logo_source_entries(
        source="coingecko",
        chain_id=1,
        entries=[
            TokenLogoSourceEntry(
                source="coingecko",
                chain_id=1,
                address=USDC,
                logo_url="https://assets.coingecko.com/coins/images/1/thumb/usdc.png",
            )
        ],
        synced_at=2,
    )
    due = cache.get_due_logos(now_ms=2, limit=32)
    assert [(item.chain_id, item.address) for item in due] == [(1, USDC)]

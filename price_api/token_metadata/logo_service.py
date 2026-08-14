from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

from price_api.observability.metrics import (
    record_logo_acquisition,
    set_logo_acquisition_active,
    set_logo_due_count,
    set_logo_source_refresh_age,
)
from price_api.token_metadata.cache import DueTokenLogo, TokenMetadataCache
from price_api.token_metadata.logo_acquirer import AcquisitionResult, TokenLogoAcquirer
from price_api.token_metadata.logo_sources import TokenLogoSourceManager

_LOGGER = logging.getLogger("price_api.token_logos")


@dataclass(frozen=True)
class LogoMaintenanceSettings:
    source_refresh_ms: int = 12 * 60 * 60 * 1000
    unavailable_retry_ms: int = 48 * 60 * 60 * 1000
    unavailable_jitter_ratio: float = 0.10
    transient_base_ms: int = 5 * 60 * 1000
    transient_cap_ms: int = 6 * 60 * 60 * 1000
    retry_after_cap_ms: int = 24 * 60 * 60 * 1000
    due_batch_size: int = 32
    concurrency: int = 8
    scheduler_poll_ms: int = 60 * 1000
    prewarm_deadline_ms: int = 30 * 60 * 1000
    shutdown_grace_ms: int = 10 * 1000


DEFAULT_LOGO_MAINTENANCE_SETTINGS = LogoMaintenanceSettings()


class TokenLogoService:
    def __init__(
        self,
        *,
        cache: TokenMetadataCache,
        source_manager: TokenLogoSourceManager,
        acquirer: TokenLogoAcquirer | None = None,
        settings: LogoMaintenanceSettings = DEFAULT_LOGO_MAINTENANCE_SETTINGS,
        rng: random.Random | None = None,
    ) -> None:
        self._cache = cache
        self._source_manager = source_manager
        self._acquirer = acquirer or TokenLogoAcquirer(source_manager=source_manager)
        self._settings = settings
        self._rng = rng or random.SystemRandom()
        self._chain_ids: list[int] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._active = 0

    async def start(self, *, chain_ids: list[int]) -> None:
        if self._task is not None:
            return
        self._chain_ids = list(dict.fromkeys(chain_ids))
        self._stop.clear()
        self._task = asyncio.create_task(
            self._maintenance_loop(),
            name="token-logo-maintenance",
        )

    async def aclose(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self._settings.shutdown_grace_ms / 1000,
                )
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._task = None
        await self._acquirer.aclose()

    async def run_once(self, *, force_source_refresh: bool = False) -> None:
        await self._source_manager.refresh_sources(
            chain_ids=self._chain_ids,
            refresh_interval_ms=self._settings.source_refresh_ms,
            force=force_source_refresh,
        )
        await self._update_source_metrics()

        now = _now_ms()
        due_count = await asyncio.to_thread(self._cache.count_due_logos, now_ms=now)
        set_logo_due_count(due_count)
        due = await asyncio.to_thread(
            self._cache.get_due_logos,
            now_ms=now,
            limit=self._settings.due_batch_size,
        )
        if not due:
            return

        semaphore = asyncio.Semaphore(self._settings.concurrency)

        async def process(item: DueTokenLogo) -> None:
            async with semaphore:
                await self._process_due(item)

        await asyncio.gather(*(process(item) for item in due))
        remaining = await asyncio.to_thread(self._cache.count_due_logos, now_ms=_now_ms())
        set_logo_due_count(remaining)

    async def _maintenance_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("token_logo_maintenance_iteration_failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._settings.scheduler_poll_ms / 1000,
                )
            except TimeoutError:
                pass

    async def _process_due(self, item: DueTokenLogo) -> None:
        self._active += 1
        set_logo_acquisition_active(self._active)
        try:
            result = await self._acquirer.acquire(
                chain_id=item.chain_id,
                address=item.address,
            )
            attempted_at = _now_ms()
            await self._persist_result(item=item, result=result, attempted_at=attempted_at)
            record_logo_acquisition(
                outcome=result.outcome,
                source=result.source or "none",
            )
            _LOGGER.info(
                "token_logo_acquisition_completed",
                extra={
                    "chain_id": item.chain_id,
                    "address": item.address,
                    "outcome": result.outcome,
                    "source": result.source,
                    "error_code": result.error_code,
                    "http_status": result.http_status,
                },
            )
        finally:
            self._active -= 1
            set_logo_acquisition_active(self._active)

    async def _persist_result(
        self,
        *,
        item: DueTokenLogo,
        result: AcquisitionResult,
        attempted_at: int,
    ) -> None:
        if result.outcome == "success":
            if result.asset is None or result.source is None:
                raise RuntimeError("successful logo acquisition is missing its asset or source")
            await asyncio.to_thread(
                self._cache.record_logo_success,
                chain_id=item.chain_id,
                address=item.address,
                image_bytes=result.asset.image_bytes,
                content_hash=result.asset.content_hash,
                mime_type=result.asset.mime_type,
                source=result.source,
                attempted_at=attempted_at,
                http_status=result.http_status,
            )
            return

        if result.outcome == "transient":
            failure_count = item.failure_count + 1
            delay = transient_retry_delay_ms(
                failure_count=failure_count,
                retry_after_ms=result.retry_after_ms,
                settings=self._settings,
                rng=self._rng,
            )
        else:
            failure_count = 0
            delay = unavailable_retry_delay_ms(settings=self._settings, rng=self._rng)

        await asyncio.to_thread(
            self._cache.record_logo_failure,
            chain_id=item.chain_id,
            address=item.address,
            outcome=result.outcome,
            attempted_at=attempted_at,
            next_attempt_at=attempted_at + delay,
            failure_count=failure_count,
            http_status=result.http_status,
            error_code=result.error_code[:64],
        )

    async def _update_source_metrics(self) -> None:
        now = _now_ms()
        for chain_id in self._chain_ids:
            for source in self._source_manager.sources:
                if source.metadata_url is None or not source.supports_chain(chain_id):
                    continue
                state = await asyncio.to_thread(
                    self._cache.get_logo_source_sync_state,
                    source=source.id,
                    chain_id=chain_id,
                )
                if state is not None:
                    set_logo_source_refresh_age(
                        source=source.id,
                        chain_id=chain_id,
                        age_seconds=max((now - state.synced_at) / 1000, 0),
                    )


def transient_retry_delay_ms(
    *,
    failure_count: int,
    retry_after_ms: int | None,
    settings: LogoMaintenanceSettings,
    rng: random.Random,
) -> int:
    exponent = min(max(failure_count - 1, 0), 31)
    backoff_cap = min(
        settings.transient_base_ms * (2**exponent),
        settings.transient_cap_ms,
    )
    jittered = rng.randint(0, backoff_cap)
    if retry_after_ms is None:
        return jittered
    bounded_retry_after = min(max(retry_after_ms, 0), settings.retry_after_cap_ms)
    return min(max(jittered, bounded_retry_after), settings.retry_after_cap_ms)


def unavailable_retry_delay_ms(
    *,
    settings: LogoMaintenanceSettings,
    rng: random.Random,
) -> int:
    span = int(settings.unavailable_retry_ms * settings.unavailable_jitter_ratio)
    return settings.unavailable_retry_ms + rng.randint(-span, span)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000

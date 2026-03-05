from __future__ import annotations

import threading
import time

from token_price_agg.security.models import RateLimitResult

_RETENTION_SECONDS = 3
_CLEANUP_INTERVAL_SECONDS = 10


class AnonymousRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}
        self._next_cleanup_ts = 0

    def consume(
        self,
        *,
        client_id: str,
        min_interval_seconds: int,
        now_ts: int | None = None,
    ) -> RateLimitResult:
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be > 0")

        now = now_ts if now_ts is not None else int(time.time())
        window_start = (now // min_interval_seconds) * min_interval_seconds
        bucket_key = client_id or "unknown"

        with self._lock:
            existing = self._windows.get(bucket_key)
            if existing is not None and existing[0] == window_start:
                request_count = existing[1] + 1
            else:
                request_count = 1
            self._windows[bucket_key] = (window_start, request_count)

            if now >= self._next_cleanup_ts:
                self._cleanup(now=now, min_interval_seconds=min_interval_seconds)
                self._next_cleanup_ts = now + _CLEANUP_INTERVAL_SECONDS

        reset_epoch = window_start + min_interval_seconds
        retry_after = max(reset_epoch - now, 0)
        allowed = request_count <= 1
        remaining = max(1 - request_count, 0)

        return RateLimitResult(
            allowed=allowed,
            limit=1,
            remaining=remaining,
            reset_epoch=reset_epoch,
            retry_after_seconds=retry_after,
            request_count=request_count,
        )

    def _cleanup(self, *, now: int, min_interval_seconds: int) -> None:
        cutoff = now - max(_RETENTION_SECONDS, min_interval_seconds * 3)
        stale_keys = [
            key
            for key, (window_start, _) in self._windows.items()
            if window_start < cutoff
        ]
        for key in stale_keys:
            self._windows.pop(key, None)

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import TypeAlias

import httpx

from price_api.observability.metrics import record_provider_transport_recycle

ParamScalar: TypeAlias = str | int | float | bool | None
QueryParams: TypeAlias = dict[str, ParamScalar]
JsonBody: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    json_data: dict[str, object] | list[object] | None
    headers: dict[str, str]


class HttpClient:
    def __init__(
        self,
        *,
        timeout_ms: int,
        max_retries: int,
        trust_env: bool = False,
        max_connections: int = 100,
        max_keepalive_connections: int | None = None,
        keepalive_expiry_s: float = 5.0,
        pool_timeout_ms: int | None = None,
        connect_timeout_ms: int | None = None,
        read_timeout_ms: int | None = None,
        write_timeout_ms: int | None = None,
        client_ttl_s: int = 300,
        client_max_requests: int = 5000,
        recycle_pool_timeout_threshold: int = 10,
        recycle_window_s: int = 30,
        provider_id: str = "unknown",
    ) -> None:
        self._timeout_ms = timeout_ms
        self._pool_timeout_ms = pool_timeout_ms if pool_timeout_ms is not None else 25
        self._connect_timeout_ms = (
            connect_timeout_ms if connect_timeout_ms is not None else timeout_ms
        )
        self._read_timeout_ms = read_timeout_ms if read_timeout_ms is not None else timeout_ms
        self._write_timeout_ms = write_timeout_ms if write_timeout_ms is not None else timeout_ms
        # Retry means additional attempts, so total attempts is retries + 1.
        self._attempts = max(1, max_retries + 1)
        self._trust_env = trust_env
        self._max_connections = max(1, max_connections)
        requested_keepalive_connections = (
            max_keepalive_connections if max_keepalive_connections is not None else 20
        )
        self._max_keepalive_connections = min(
            self._max_connections,
            max(0, requested_keepalive_connections),
        )
        self._keepalive_expiry_s = keepalive_expiry_s
        self._client_ttl_s = client_ttl_s
        self._client_max_requests = client_max_requests
        self._recycle_pool_timeout_threshold = recycle_pool_timeout_threshold
        self._recycle_window_s = recycle_window_s
        self._provider_id = provider_id
        self._client = self._new_client()
        self._created_at = time.monotonic()
        self._request_count = 0
        self._pool_timeout_events: deque[float] = deque()
        self._recycle_lock = asyncio.Lock()
        self._last_recycle_reason: str | None = None
        self._last_recycle_at: float | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _new_client(self) -> httpx.AsyncClient:
        # Do not inherit ambient proxy/TLS env unless the deployment explicitly opts in.
        limits = httpx.Limits(
            max_connections=self._max_connections,
            max_keepalive_connections=self._max_keepalive_connections,
            keepalive_expiry=self._keepalive_expiry_s,
        )
        timeout = self._timeout(
            read_timeout_ms=self._read_timeout_ms,
            pool_timeout_ms=self._pool_timeout_ms,
        )
        return httpx.AsyncClient(
            timeout=timeout,
            trust_env=self._trust_env,
            limits=limits,
        )

    @property
    def trust_env(self) -> bool:
        return self._trust_env

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    @property
    def max_connections(self) -> int:
        return self._max_connections

    @property
    def max_keepalive_connections(self) -> int:
        return self._max_keepalive_connections

    @property
    def keepalive_expiry_s(self) -> float:
        return self._keepalive_expiry_s

    @property
    def pool_timeout_ms(self) -> int:
        return self._pool_timeout_ms

    @property
    def read_timeout_ms(self) -> int:
        return self._read_timeout_ms

    def recent_pool_timeout_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self._recycle_window_s
        return sum(1 for event_at in self._pool_timeout_events if event_at >= cutoff)

    def recently_recycled_due_to_pool_timeout(self) -> bool:
        return self.recently_recycled(reason="pool_timeout")

    def recently_recycled(self, *, reason: str | None = None) -> bool:
        if self._last_recycle_at is None:
            return False
        if reason is not None and self._last_recycle_reason != reason:
            return False
        return time.monotonic() - self._last_recycle_at <= self._recycle_window_s

    async def close(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        await self._client.aclose()

    async def record_pool_timeout(self) -> None:
        now = time.monotonic()
        self._pool_timeout_events.append(now)
        cutoff = now - self._recycle_window_s
        while self._pool_timeout_events and self._pool_timeout_events[0] < cutoff:
            self._pool_timeout_events.popleft()
        if len(self._pool_timeout_events) >= self._recycle_pool_timeout_threshold:
            self._pool_timeout_events.clear()
            await self.recycle(reason="pool_timeout")

    async def recycle(self, *, reason: str) -> None:
        async with self._recycle_lock:
            self._replace_client_locked(reason=reason)

    async def _close_after_grace(self, client: httpx.AsyncClient) -> None:
        grace_s = max(self._read_timeout_ms, self._connect_timeout_ms, self._write_timeout_ms)
        await asyncio.sleep((grace_s / 1000) + 0.1)
        await client.aclose()

    async def get(
        self,
        *,
        url: str,
        params: QueryParams | None = None,
        headers: dict[str, str] | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        response = await self._retryable_request(
            method="GET",
            url=url,
            params=params,
            headers=headers,
            timeout_ms=timeout_ms,
        )
        return self._to_http_response(response)

    async def post(
        self,
        *,
        url: str,
        json: JsonBody | None = None,
        params: QueryParams | None = None,
        headers: dict[str, str] | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        response = await self._retryable_request(
            method="POST",
            url=url,
            params=params,
            headers=headers,
            json=json,
            timeout_ms=timeout_ms,
        )
        return self._to_http_response(response)

    @staticmethod
    def _to_http_response(response: httpx.Response) -> HttpResponse:
        json_data: dict[str, object] | list[object] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, (dict, list)):
                json_data = parsed
        except ValueError:
            json_data = None

        return HttpResponse(
            status_code=response.status_code,
            json_data=json_data,
            headers={key.lower(): value for key, value in response.headers.items()},
        )

    async def _retryable_request(
        self,
        *,
        method: str,
        url: str,
        params: QueryParams | None,
        headers: dict[str, str] | None,
        json: JsonBody | None = None,
        timeout_ms: int | None = None,
    ) -> httpx.Response:
        await self._recycle_if_needed()
        client = self._client
        self._request_count += 1
        effective_timeout = self._timeout(
            read_timeout_ms=timeout_ms or self._read_timeout_ms,
            pool_timeout_ms=self._pool_timeout_ms,
        )
        last_exc: Exception | None = None
        for _ in range(self._attempts):
            try:
                return await client.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=headers,
                    json=json,
                    timeout=effective_timeout,
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
                last_exc = exc
                continue

        if last_exc is None:
            raise RuntimeError("HTTP retry loop exhausted unexpectedly")
        raise last_exc

    async def _recycle_if_needed(self) -> None:
        now = time.monotonic()
        if now - self._created_at >= self._client_ttl_s:
            async with self._recycle_lock:
                if time.monotonic() - self._created_at >= self._client_ttl_s:
                    self._replace_client_locked(reason="ttl")
            return
        if self._request_count >= self._client_max_requests:
            async with self._recycle_lock:
                if self._request_count >= self._client_max_requests:
                    self._replace_client_locked(reason="max_requests")

    def _replace_client_locked(self, *, reason: str) -> None:
        old_client = self._client
        self._client = self._new_client()
        self._created_at = time.monotonic()
        self._request_count = 0
        self._last_recycle_reason = reason
        self._last_recycle_at = self._created_at
        record_provider_transport_recycle(provider=self._provider_id, reason=reason)
        task = asyncio.create_task(self._close_after_grace(old_client))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _timeout(self, *, read_timeout_ms: int, pool_timeout_ms: int) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._connect_timeout_ms / 1000,
            read=read_timeout_ms / 1000,
            write=self._write_timeout_ms / 1000,
            pool=pool_timeout_ms / 1000,
        )

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

import httpx

ParamScalar: TypeAlias = str | int | float | bool | None
QueryParams: TypeAlias = dict[str, ParamScalar]
JsonBody: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    json_data: dict[str, object] | list[object] | None
    text: str


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
    ) -> None:
        self._timeout_ms = timeout_ms
        self._timeout = timeout_ms / 1000
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
        # Do not inherit ambient proxy/TLS env unless the deployment explicitly opts in.
        limits = httpx.Limits(
            max_connections=self._max_connections,
            max_keepalive_connections=self._max_keepalive_connections,
            keepalive_expiry=self._keepalive_expiry_s,
        )
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            trust_env=trust_env,
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

    async def close(self) -> None:
        await self._client.aclose()

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
            status_code=response.status_code, json_data=json_data, text=response.text
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
        effective_timeout = (timeout_ms / 1000) if timeout_ms is not None else self._timeout
        last_exc: Exception | None = None
        for _ in range(self._attempts):
            try:
                return await self._client.request(
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

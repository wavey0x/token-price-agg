from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, TypeVar

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import ErrorInfo, ProviderStatus
from token_price_agg.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from token_price_agg.observability.metrics import record_provider_call

_LOGGER = logging.getLogger("token_price_agg.aggregator")

TReq = TypeVar("TReq", ProviderPriceRequest, ProviderQuoteRequest)
TResult = TypeVar("TResult", PriceResult, QuoteResult)


class ProviderOperationRunner:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._global_semaphore = asyncio.Semaphore(settings.provider_global_limit)

    async def run_prices(
        self,
        *,
        plugins: Sequence[object],
        req: ProviderPriceRequest,
        deadline_ms: int,
    ) -> list[PriceResult]:
        return await self._run_operation(
            plugins=plugins,
            req=req,
            deadline_ms=deadline_ms,
            operation="price",
            run_single_plugin=lambda plugin, op_req, fanout_semaphore: self._run_price_plugin(
                plugin,
                op_req,
                fanout_semaphore=fanout_semaphore,
            ),
            deadline_result=lambda provider_id, op_req, timeout_ms: self._deadline_price_result(
                provider_id=provider_id,
                req=op_req,
                deadline_ms=timeout_ms,
            ),
            internal_task_failure_result=(
                lambda provider_id, op_req, exc: self._internal_price_task_failure_result(
                    provider_id=provider_id,
                    req=op_req,
                    exc=exc,
                )
            ),
        )

    async def run_quotes(
        self,
        *,
        plugins: Sequence[object],
        req: ProviderQuoteRequest,
        deadline_ms: int,
    ) -> list[QuoteResult]:
        return await self._run_operation(
            plugins=plugins,
            req=req,
            deadline_ms=deadline_ms,
            operation="quote",
            run_single_plugin=lambda plugin, op_req, fanout_semaphore: self._run_quote_plugin(
                plugin,
                op_req,
                fanout_semaphore=fanout_semaphore,
            ),
            deadline_result=lambda provider_id, op_req, timeout_ms: self._deadline_quote_result(
                provider_id=provider_id,
                req=op_req,
                deadline_ms=timeout_ms,
            ),
            internal_task_failure_result=(
                lambda provider_id, op_req, exc: self._internal_quote_task_failure_result(
                    provider_id=provider_id,
                    req=op_req,
                    exc=exc,
                )
            ),
        )

    async def _run_operation(
        self,
        *,
        plugins: Sequence[object],
        req: TReq,
        deadline_ms: int,
        operation: str,
        run_single_plugin: Callable[
            [object, TReq, asyncio.Semaphore],
            Coroutine[Any, Any, TResult],
        ],
        deadline_result: Callable[[str, TReq, int], TResult],
        internal_task_failure_result: Callable[[str, TReq, Exception], TResult],
    ) -> list[TResult]:
        fanout_semaphore = asyncio.Semaphore(self._settings.provider_fanout_per_request)
        deadline_s = max(deadline_ms, 1) / 1000
        tasks: dict[asyncio.Task[TResult], str] = {}
        for plugin in plugins:
            provider_id = getattr(plugin, "id", "unknown")
            task: asyncio.Task[TResult] = asyncio.create_task(
                run_single_plugin(plugin, req, fanout_semaphore)
            )
            tasks[task] = provider_id

        done, pending = await asyncio.wait(tasks.keys(), timeout=deadline_s)
        results: list[TResult] = []

        for task in done:
            provider_id = tasks[task]
            try:
                result = task.result()
            except asyncio.CancelledError:
                result = deadline_result(provider_id, req, deadline_ms)
            except Exception as exc:
                _LOGGER.exception(
                    f"provider_{operation}_task_failed",
                    extra={"provider": provider_id, "operation": operation},
                )
                result = internal_task_failure_result(provider_id, req, exc)
            results.append(result)

        for task in pending:
            provider_id = tasks[task]
            task.cancel()
            results.append(deadline_result(provider_id, req, deadline_ms))

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        return results

    async def _run_price_plugin(
        self,
        plugin: object,
        req: ProviderPriceRequest,
        *,
        fanout_semaphore: asyncio.Semaphore,
    ) -> PriceResult:
        from token_price_agg.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)

        if not plugin.supports_price:
            result = PriceResult(
                provider=plugin.id,
                status=ProviderStatus.INVALID_REQUEST,
                token=req.token,
                latency_ms=0,
                error=ErrorInfo(
                    code="UNSUPPORTED_OPERATION",
                    message="Provider does not support price",
                ),
            )
            self._record_price_result(result=result, latency_ms=0)
            return result

        if not plugin.available:
            result = PriceResult(
                provider=plugin.id,
                status=ProviderStatus.INVALID_REQUEST,
                token=req.token,
                latency_ms=0,
                error=ErrorInfo(
                    code="PROVIDER_UNAVAILABLE",
                    message=plugin.unavailable_reason or "Provider unavailable",
                ),
            )
            self._record_price_result(result=result, latency_ms=0)
            return result

        async with self._global_semaphore:
            async with fanout_semaphore:
                started = time.perf_counter()
                try:
                    result = await plugin.get_price(req)
                    if not isinstance(result, PriceResult):
                        raise TypeError("Provider returned non-PriceResult response")
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    _LOGGER.exception(
                        "provider_price_call_failed",
                        extra={"provider": plugin.id, "operation": "price"},
                    )
                    result = PriceResult(
                        provider=plugin.id,
                        status=ProviderStatus.INTERNAL_ERROR,
                        token=req.token,
                        latency_ms=elapsed_ms,
                        error=ErrorInfo(
                            code="INTERNAL_ERROR",
                            message=f"Provider execution failed: {type(exc).__name__}",
                        ),
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_price_result(
                    result=result,
                    latency_ms=max(result.latency_ms, elapsed_ms),
                )
                return result

    async def _run_quote_plugin(
        self,
        plugin: object,
        req: ProviderQuoteRequest,
        *,
        fanout_semaphore: asyncio.Semaphore,
    ) -> QuoteResult:
        from token_price_agg.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)

        if not plugin.supports_quote:
            result = QuoteResult(
                provider=plugin.id,
                status=ProviderStatus.INVALID_REQUEST,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=0,
                error=ErrorInfo(
                    code="UNSUPPORTED_OPERATION",
                    message="Provider does not support quote",
                ),
            )
            self._record_quote_result(result=result, latency_ms=0)
            return result

        if not plugin.available:
            result = QuoteResult(
                provider=plugin.id,
                status=ProviderStatus.INVALID_REQUEST,
                token_in=req.token_in,
                token_out=req.token_out,
                amount_in=req.amount_in,
                latency_ms=0,
                error=ErrorInfo(
                    code="PROVIDER_UNAVAILABLE",
                    message=plugin.unavailable_reason or "Provider unavailable",
                ),
            )
            self._record_quote_result(result=result, latency_ms=0)
            return result

        async with self._global_semaphore:
            async with fanout_semaphore:
                started = time.perf_counter()
                try:
                    result = await plugin.get_quote(req)
                    if not isinstance(result, QuoteResult):
                        raise TypeError("Provider returned non-QuoteResult response")
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    _LOGGER.exception(
                        "provider_quote_call_failed",
                        extra={"provider": plugin.id, "operation": "quote"},
                    )
                    result = QuoteResult(
                        provider=plugin.id,
                        status=ProviderStatus.INTERNAL_ERROR,
                        token_in=req.token_in,
                        token_out=req.token_out,
                        amount_in=req.amount_in,
                        latency_ms=elapsed_ms,
                        error=ErrorInfo(
                            code="INTERNAL_ERROR",
                            message=f"Provider execution failed: {type(exc).__name__}",
                        ),
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_quote_result(
                    result=result,
                    latency_ms=max(result.latency_ms, elapsed_ms),
                )
                return result

    def _deadline_price_result(
        self,
        *,
        provider_id: str,
        req: ProviderPriceRequest,
        deadline_ms: int,
    ) -> PriceResult:
        result = PriceResult(
            provider=provider_id,
            status=ProviderStatus.TIMEOUT,
            token=req.token,
            latency_ms=deadline_ms,
            error=ErrorInfo(
                code="DEADLINE_EXCEEDED",
                message="Provider exceeded aggregate deadline",
            ),
        )
        self._record_price_result(result=result, latency_ms=result.latency_ms)
        return result

    def _deadline_quote_result(
        self,
        *,
        provider_id: str,
        req: ProviderQuoteRequest,
        deadline_ms: int,
    ) -> QuoteResult:
        result = QuoteResult(
            provider=provider_id,
            status=ProviderStatus.TIMEOUT,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            latency_ms=deadline_ms,
            error=ErrorInfo(
                code="DEADLINE_EXCEEDED",
                message="Provider exceeded aggregate deadline",
            ),
        )
        self._record_quote_result(result=result, latency_ms=result.latency_ms)
        return result

    def _internal_price_task_failure_result(
        self,
        *,
        provider_id: str,
        req: ProviderPriceRequest,
        exc: Exception,
    ) -> PriceResult:
        result = PriceResult(
            provider=provider_id,
            status=ProviderStatus.INTERNAL_ERROR,
            token=req.token,
            latency_ms=0,
            error=ErrorInfo(
                code="INTERNAL_ERROR",
                message=f"Provider task failed: {type(exc).__name__}",
            ),
        )
        self._record_price_result(result=result, latency_ms=0)
        return result

    def _internal_quote_task_failure_result(
        self,
        *,
        provider_id: str,
        req: ProviderQuoteRequest,
        exc: Exception,
    ) -> QuoteResult:
        result = QuoteResult(
            provider=provider_id,
            status=ProviderStatus.INTERNAL_ERROR,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            latency_ms=0,
            error=ErrorInfo(
                code="INTERNAL_ERROR",
                message=f"Provider task failed: {type(exc).__name__}",
            ),
        )
        self._record_quote_result(result=result, latency_ms=0)
        return result

    @staticmethod
    def _record_price_result(*, result: PriceResult, latency_ms: int) -> None:
        record_provider_call(
            provider=result.provider,
            operation="price",
            status=result.status.value,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _record_quote_result(*, result: QuoteResult, latency_ms: int) -> None:
        record_provider_call(
            provider=result.provider,
            operation="quote",
            status=result.status.value,
            latency_ms=latency_ms,
        )

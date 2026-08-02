from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from price_api.app.config import Settings
from price_api.core.circuit import CircuitBreaker
from price_api.core.errors import (
    AdmissionRejectedError,
    ErrorInfo,
    ErrorType,
    ProviderStatus,
)
from price_api.core.limits import CapacityLimiters, LimitReservation
from price_api.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)
from price_api.observability.metrics import (
    dec_provider_inflight_call,
    inc_provider_inflight_call,
    record_admission_rejection,
    record_provider_call,
    set_admission_inflight_units,
)

_LOGGER = logging.getLogger("price_api.aggregator")

TReq = TypeVar("TReq", ProviderPriceRequest, ProviderQuoteRequest)
TResult = TypeVar("TResult", PriceResult, QuoteResult)


class ProviderOperationRunner:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._capacity = CapacityLimiters(
            global_units=settings.effective_provider_global_units,
            per_provider_units=settings.provider_per_provider_units,
        )
        self._circuit = CircuitBreaker(
            failure_window_s=settings.provider_circuit_failure_window_s,
            failure_threshold=settings.provider_circuit_failure_threshold,
            open_duration_s=settings.provider_circuit_open_duration_s,
            half_open_probe_count=settings.provider_circuit_half_open_probe_count,
        )

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
            static_result=lambda plugin, op_req, reason: self._static_price_result(
                plugin=plugin,
                req=op_req,
                reason=reason,
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
            static_result=lambda plugin, op_req, reason: self._static_quote_result(
                plugin=plugin,
                req=op_req,
                reason=reason,
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
        static_result: Callable[[object, TReq, str], TResult],
        deadline_result: Callable[[str, TReq, int], TResult],
        internal_task_failure_result: Callable[[str, TReq, Exception], TResult],
    ) -> list[TResult]:
        from price_api.providers.base import ProviderPlugin

        immediate_results: list[TResult] = []
        runnable: list[_RunnableProvider] = []
        provider_reservations: list[LimitReservation] = []
        circuit_allowed_provider_ids: set[str] = set()
        global_reservation: LimitReservation | None = None
        tasks: dict[asyncio.Task[TResult], str] = {}

        try:
            for plugin in plugins:
                assert isinstance(plugin, ProviderPlugin)
                provider_id = plugin.id

                static_reason = self._static_rejection_reason(plugin=plugin, operation=operation)
                if static_reason is not None:
                    immediate_results.append(static_result(plugin, req, static_reason))
                    continue

                if not self._circuit.allow(provider_id):
                    immediate_results.append(static_result(plugin, req, "circuit_open"))
                    continue
                circuit_allowed_provider_ids.add(provider_id)

                provider_reservation = await self._capacity.provider_limiter(
                    provider_id
                ).try_acquire(
                    units=1,
                    timeout_ms=self._settings.admission_acquire_timeout_ms,
                )
                if provider_reservation is None:
                    immediate_results.append(static_result(plugin, req, "provider_capacity"))
                    continue
                provider_reservations.append(provider_reservation)
                runnable.append(_RunnableProvider(plugin=plugin, provider_id=provider_id))

            work_units = len(runnable)
            if work_units > 0:
                global_reservation = await self._capacity.global_limiter.try_acquire(
                    units=work_units,
                    timeout_ms=self._settings.admission_acquire_timeout_ms,
                )
                if global_reservation is None:
                    record_admission_rejection(reason="global_capacity", operation=operation)
                    raise AdmissionRejectedError(
                        "SERVICE_OVERLOADED",
                        "Provider capacity exhausted",
                        status_code=503,
                    )

            self._sync_capacity_metrics(operation=operation)

            fanout_semaphore = asyncio.Semaphore(self._settings.provider_fanout_per_request)
            deadline_s = max(deadline_ms, 1) / 1000
            for runnable_provider in runnable:
                task: asyncio.Task[TResult] = asyncio.create_task(
                    run_single_plugin(runnable_provider.plugin, req, fanout_semaphore)
                )
                tasks[task] = runnable_provider.provider_id

            results = await self._wait_for_results(
                tasks=tasks,
                req=req,
                deadline_ms=deadline_ms,
                deadline_s=deadline_s,
                operation=operation,
                deadline_result=deadline_result,
                internal_task_failure_result=internal_task_failure_result,
            )
            results.extend(immediate_results)
            for result in results:
                self._circuit.record_result(result)
            return results
        finally:
            pending_tasks = [task for task in tasks if not task.done()]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            self._release_circuit_probes(circuit_allowed_provider_ids)
            await _release_all(
                [
                    *(provider_reservations or []),
                    *(item for item in [global_reservation] if item),
                ]
            )
            self._sync_capacity_metrics(operation=operation)

    async def _wait_for_results(
        self,
        *,
        tasks: dict[asyncio.Task[TResult], str],
        req: TReq,
        deadline_ms: int,
        deadline_s: float,
        operation: str,
        deadline_result: Callable[[str, TReq, int], TResult],
        internal_task_failure_result: Callable[[str, TReq, Exception], TResult],
    ) -> list[TResult]:
        if not tasks:
            return []

        try:
            done, pending = await asyncio.wait(tasks.keys(), timeout=deadline_s)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.keys(), return_exceptions=True)
            raise

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
        from price_api.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)

        async with fanout_semaphore:
            inc_provider_inflight_call(provider=plugin.id, operation="price")
            started = time.perf_counter()
            try:
                try:
                    result = await plugin.get_price(req)
                    if not isinstance(result, PriceResult):
                        raise TypeError("Provider returned non-PriceResult response")
                    _validate_price_result_identity(result=result, req=req, provider_id=plugin.id)
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    _LOGGER.exception(
                        "provider_price_call_failed",
                        extra={"provider": plugin.id, "operation": "price"},
                    )
                    result = PriceResult(
                        provider=plugin.id,
                        status=ProviderStatus.ERROR,
                        token=req.token,
                        latency_ms=elapsed_ms,
                        error=ErrorInfo(
                            type=ErrorType.INTERNAL,
                            message=f"Provider execution failed: {type(exc).__name__}",
                        ),
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_price_result(
                    result=result,
                    latency_ms=max(result.latency_ms, elapsed_ms),
                )
                return result
            finally:
                dec_provider_inflight_call(provider=plugin.id, operation="price")

    async def _run_quote_plugin(
        self,
        plugin: object,
        req: ProviderQuoteRequest,
        *,
        fanout_semaphore: asyncio.Semaphore,
    ) -> QuoteResult:
        from price_api.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)

        async with fanout_semaphore:
            inc_provider_inflight_call(provider=plugin.id, operation="quote")
            started = time.perf_counter()
            try:
                try:
                    result = await plugin.get_quote(req)
                    if not isinstance(result, QuoteResult):
                        raise TypeError("Provider returned non-QuoteResult response")
                    _validate_quote_result_identity(result=result, req=req, provider_id=plugin.id)
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    _LOGGER.exception(
                        "provider_quote_call_failed",
                        extra={"provider": plugin.id, "operation": "quote"},
                    )
                    result = QuoteResult(
                        provider=plugin.id,
                        status=ProviderStatus.ERROR,
                        token_in=req.token_in,
                        token_out=req.token_out,
                        amount_in=req.amount_in,
                        latency_ms=elapsed_ms,
                        error=ErrorInfo(
                            type=ErrorType.INTERNAL,
                            message=f"Provider execution failed: {type(exc).__name__}",
                        ),
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._record_quote_result(
                    result=result,
                    latency_ms=max(result.latency_ms, elapsed_ms),
                )
                return result
            finally:
                dec_provider_inflight_call(provider=plugin.id, operation="quote")

    @staticmethod
    def _static_rejection_reason(*, plugin: object, operation: str) -> str | None:
        from price_api.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)
        if operation == "price" and not plugin.supports_price:
            return "unsupported_operation"
        if operation == "quote" and not plugin.supports_quote:
            return "unsupported_operation"
        if not plugin.available:
            return "provider_unavailable"
        return None

    def _static_price_result(
        self,
        *,
        plugin: object,
        req: ProviderPriceRequest,
        reason: str,
    ) -> PriceResult:
        from price_api.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)
        status, error = _static_error(plugin=plugin, reason=reason)
        result = PriceResult(
            provider=plugin.id,
            status=status,
            token=req.token,
            latency_ms=0,
            error=error,
        )
        self._record_price_result(result=result, latency_ms=0)
        return result

    def _static_quote_result(
        self,
        *,
        plugin: object,
        req: ProviderQuoteRequest,
        reason: str,
    ) -> QuoteResult:
        from price_api.providers.base import ProviderPlugin

        assert isinstance(plugin, ProviderPlugin)
        status, error = _static_error(plugin=plugin, reason=reason)
        result = QuoteResult(
            provider=plugin.id,
            status=status,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            latency_ms=0,
            error=error,
        )
        self._record_quote_result(result=result, latency_ms=0)
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
            status=ProviderStatus.ERROR,
            token=req.token,
            latency_ms=deadline_ms,
            error=ErrorInfo(
                type=ErrorType.DEADLINE_EXCEEDED,
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
            status=ProviderStatus.ERROR,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            latency_ms=deadline_ms,
            error=ErrorInfo(
                type=ErrorType.DEADLINE_EXCEEDED,
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
            status=ProviderStatus.ERROR,
            token=req.token,
            latency_ms=0,
            error=ErrorInfo(
                type=ErrorType.INTERNAL,
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
            status=ProviderStatus.ERROR,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            latency_ms=0,
            error=ErrorInfo(
                type=ErrorType.INTERNAL,
                message=f"Provider task failed: {type(exc).__name__}",
            ),
        )
        self._record_quote_result(result=result, latency_ms=0)
        return result

    def _sync_capacity_metrics(self, *, operation: str) -> None:
        set_admission_inflight_units(
            scope="global",
            operation=operation,
            units=self._capacity.global_limiter.used,
        )
        set_admission_inflight_units(
            scope="provider",
            operation=operation,
            units=self._capacity.provider_used_units(),
        )

    def circuit_open_providers(self) -> set[str]:
        return self._circuit.circuit_open_providers()

    def _release_circuit_probes(self, provider_ids: set[str]) -> None:
        for provider_id in provider_ids:
            self._circuit.release_probe(provider_id)

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


@dataclass(frozen=True, slots=True)
class _RunnableProvider:
    plugin: object
    provider_id: str


def _static_error(*, plugin: object, reason: str) -> tuple[ProviderStatus, ErrorInfo]:
    from price_api.providers.base import ProviderPlugin

    assert isinstance(plugin, ProviderPlugin)
    if reason == "unsupported_operation":
        return (
            ProviderStatus.BAD_REQUEST,
            ErrorInfo(
                type=ErrorType.UNSUPPORTED_OPERATION,
                message="Provider does not support operation",
            ),
        )
    if reason == "provider_unavailable":
        return (
            ProviderStatus.BAD_REQUEST,
            ErrorInfo(
                type=ErrorType.PROVIDER_UNAVAILABLE,
                message=plugin.unavailable_reason or "Provider unavailable",
            ),
        )
    if reason == "circuit_open":
        return (
            ProviderStatus.ERROR,
            ErrorInfo(
                type=ErrorType.PROVIDER_UNAVAILABLE,
                message="Provider circuit is open",
            ),
        )
    if reason == "provider_capacity":
        return (
            ProviderStatus.ERROR,
            ErrorInfo(
                type=ErrorType.PROVIDER_UNAVAILABLE,
                message="Provider capacity unavailable",
            ),
        )
    return (
        ProviderStatus.ERROR,
        ErrorInfo(type=ErrorType.PROVIDER_UNAVAILABLE, message="Provider unavailable"),
    )


async def _release_all(reservations: Sequence[LimitReservation | None]) -> None:
    for reservation in reservations:
        if reservation is None:
            continue
        await reservation.release()


def _validate_price_result_identity(
    *, result: PriceResult, req: ProviderPriceRequest, provider_id: str
) -> None:
    if result.provider != provider_id:
        raise ValueError("Provider result identity does not match invoked provider")
    if result.status != ProviderStatus.OK:
        return
    if result.token is None or result.token.chain_id != req.chain_id:
        raise ValueError("Successful price result has invalid token identity")
    if result.token.address != req.token.address:
        raise ValueError("Successful price result token does not match request")


def _validate_quote_result_identity(
    *, result: QuoteResult, req: ProviderQuoteRequest, provider_id: str
) -> None:
    if result.provider != provider_id:
        raise ValueError("Provider result identity does not match invoked provider")
    if result.status != ProviderStatus.OK:
        return
    if result.token_in is None or result.token_out is None:
        raise ValueError("Successful quote result is missing token identity")
    if result.token_in.chain_id != req.chain_id or result.token_out.chain_id != req.chain_id:
        raise ValueError("Successful quote result has invalid chain identity")
    if (
        result.token_in.address != req.token_in.address
        or result.token_out.address != req.token_out.address
    ):
        raise ValueError("Successful quote result tokens do not match request")
    if result.amount_in != req.amount_in:
        raise ValueError("Successful quote result amount_in does not match request")

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from price_api.app.config import Settings
from price_api.core.errors import ErrorInfo, ErrorType, InvalidRequestError, ProviderStatus
from price_api.core.limits import WeightedLimiter
from price_api.core.models import (
    PriceResult,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
    VaultContext,
)
from price_api.observability.metrics import (
    record_admission_rejection,
    record_vault_resolution,
    set_admission_inflight_units,
)
from price_api.vault.resolver import QuoteVaultResolution, VaultResolver

_LOGGER = logging.getLogger(__name__)


class VaultResolutionStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    NOT_VAULT = "not_vault"
    RESOLVED = "resolved"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VaultResolutionFailure:
    reason: str
    latency_ms: int

    def error_info(self) -> ErrorInfo:
        return ErrorInfo(
            type=ErrorType.VAULT_RESOLUTION_FAILED,
            message="Vault underlying resolution failed; provider request was not attempted",
        )


@dataclass(frozen=True, slots=True)
class ResolvedPriceRequest:
    request: ProviderPriceRequest
    status: VaultResolutionStatus
    vault_context: VaultContext | None = None
    failure: VaultResolutionFailure | None = None

    def __post_init__(self) -> None:
        if (self.status == VaultResolutionStatus.RESOLVED) != (self.vault_context is not None):
            raise ValueError("resolved price status requires exactly one vault context")
        if (self.status == VaultResolutionStatus.FAILED) != (self.failure is not None):
            raise ValueError("failed price status requires exactly one resolution failure")


@dataclass(frozen=True, slots=True)
class ResolvedQuoteRequest:
    request: ProviderQuoteRequest
    status: VaultResolutionStatus
    vault_resolution: QuoteVaultResolution | None = None
    failure: VaultResolutionFailure | None = None

    def __post_init__(self) -> None:
        if (self.status == VaultResolutionStatus.RESOLVED) != (self.vault_resolution is not None):
            raise ValueError("resolved quote status requires exactly one vault resolution")
        if (self.status == VaultResolutionStatus.FAILED) != (self.failure is not None):
            raise ValueError("failed quote status requires exactly one resolution failure")


class VaultUnderlyingService:
    def __init__(self, *, settings: Settings, vault_resolver: VaultResolver) -> None:
        self._settings = settings
        self._vault_resolver = vault_resolver
        self._limiter = WeightedLimiter(capacity=settings.vault_global_units)

    async def resolve_price(
        self,
        *,
        req: ProviderPriceRequest,
        use_underlying: bool,
    ) -> ResolvedPriceRequest:
        if not use_underlying:
            return ResolvedPriceRequest(
                request=req,
                status=VaultResolutionStatus.NOT_REQUESTED,
            )

        started = time.perf_counter()
        reservation = await self._limiter.try_acquire(
            units=1,
            timeout_ms=self._settings.admission_acquire_timeout_ms,
        )
        if reservation is None:
            record_admission_rejection(reason="vault_capacity", operation="price")
            _LOGGER.info(
                "price_use_underlying_capacity_unavailable",
                extra={"chain_id": req.chain_id, "token": req.token.address},
            )
            record_vault_resolution(
                result="capacity_unavailable",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            return _failed_price_resolution(req=req, reason="VAULT_CAPACITY", started=started)

        self._sync_inflight(operation="price")
        try:
            resolved_req, vault_context = await self._vault_resolver.resolve_price_request(req)
            if vault_context.price_per_share is None:
                _LOGGER.warning(
                    "price_use_underlying_resolution_invalid",
                    extra={
                        "chain_id": req.chain_id,
                        "token": req.token.address,
                        "resolution_error_type": "INVALID_VAULT_RATE",
                    },
                )
                return _failed_price_resolution(
                    req=req,
                    reason="INVALID_VAULT_RATE",
                    started=started,
                )
            return ResolvedPriceRequest(
                request=resolved_req,
                status=VaultResolutionStatus.RESOLVED,
                vault_context=vault_context,
            )
        except InvalidRequestError as exc:
            if exc.type == "INVALID_VAULT":
                _LOGGER.debug(
                    "price_use_underlying_token_not_vault",
                    extra={"chain_id": req.chain_id, "token": req.token.address},
                )
                return ResolvedPriceRequest(
                    request=req,
                    status=VaultResolutionStatus.NOT_VAULT,
                )
            _LOGGER.warning(
                "price_use_underlying_resolution_failed",
                extra={
                    "chain_id": req.chain_id,
                    "token": req.token.address,
                    "resolution_error_type": exc.type,
                },
            )
            return _failed_price_resolution(req=req, reason=exc.type, started=started)
        except Exception:
            _LOGGER.warning(
                "price_use_underlying_resolution_failed",
                extra={"chain_id": req.chain_id, "token": req.token.address},
                exc_info=True,
            )
            return _failed_price_resolution(req=req, reason="INTERNAL", started=started)
        finally:
            await reservation.release()
            self._sync_inflight(operation="price")

    async def resolve_quote(
        self,
        *,
        req: ProviderQuoteRequest,
        use_underlying: bool,
    ) -> ResolvedQuoteRequest:
        if not use_underlying:
            return ResolvedQuoteRequest(
                request=req,
                status=VaultResolutionStatus.NOT_REQUESTED,
            )

        started = time.perf_counter()
        reservation = await self._limiter.try_acquire(
            units=2,
            timeout_ms=self._settings.admission_acquire_timeout_ms,
        )
        if reservation is None:
            record_admission_rejection(reason="vault_capacity", operation="quote")
            _LOGGER.info(
                "quote_use_underlying_capacity_unavailable",
                extra={
                    "chain_id": req.chain_id,
                    "token_in": req.token_in.address,
                    "token_out": req.token_out.address,
                },
            )
            record_vault_resolution(
                result="capacity_unavailable",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            return _failed_quote_resolution(req=req, reason="VAULT_CAPACITY", started=started)

        self._sync_inflight(operation="quote")
        try:
            resolved_req, resolution = await self._vault_resolver.resolve_quote_request(req)
            invalid_reason = _invalid_quote_resolution_reason(resolution)
            if invalid_reason is not None:
                _LOGGER.warning(
                    "quote_use_underlying_resolution_invalid",
                    extra={
                        "chain_id": req.chain_id,
                        "token_in": req.token_in.address,
                        "token_out": req.token_out.address,
                        "resolution_error_type": invalid_reason,
                    },
                )
                return _failed_quote_resolution(
                    req=req,
                    reason=invalid_reason,
                    started=started,
                )
            return ResolvedQuoteRequest(
                request=resolved_req,
                status=VaultResolutionStatus.RESOLVED,
                vault_resolution=resolution,
            )
        except InvalidRequestError as exc:
            if exc.type == "INVALID_VAULT":
                _LOGGER.debug(
                    "quote_use_underlying_tokens_not_vaults",
                    extra={
                        "chain_id": req.chain_id,
                        "token_in": req.token_in.address,
                        "token_out": req.token_out.address,
                    },
                )
                return ResolvedQuoteRequest(
                    request=req,
                    status=VaultResolutionStatus.NOT_VAULT,
                )
            _LOGGER.warning(
                "quote_use_underlying_resolution_failed",
                extra={
                    "chain_id": req.chain_id,
                    "token_in": req.token_in.address,
                    "token_out": req.token_out.address,
                    "resolution_error_type": exc.type,
                },
            )
            return _failed_quote_resolution(req=req, reason=exc.type, started=started)
        except Exception:
            _LOGGER.warning(
                "quote_use_underlying_resolution_failed",
                extra={
                    "chain_id": req.chain_id,
                    "token_in": req.token_in.address,
                    "token_out": req.token_out.address,
                },
                exc_info=True,
            )
            return _failed_quote_resolution(req=req, reason="INTERNAL", started=started)
        finally:
            await reservation.release()
            self._sync_inflight(operation="quote")

    @staticmethod
    def apply_price_context(
        *,
        req: ProviderPriceRequest,
        results: list[PriceResult],
        vault_context: VaultContext | None,
    ) -> None:
        if vault_context is None:
            return

        try:
            multiplier = _vault_share_to_asset_multiplier(vault_context.price_per_share)
        except InvalidRequestError:
            _LOGGER.warning(
                "price_use_underlying_invalid_rate",
                extra={"chain_id": req.chain_id, "token": req.token.address},
                exc_info=True,
            )
            multiplier = None

        for result in results:
            if result.status != ProviderStatus.OK or multiplier is None:
                continue
            if result.price_usd is not None:
                result.price_usd = result.price_usd * multiplier
            result.vault_context = vault_context

    @staticmethod
    def apply_quote_resolution(
        *,
        req: ProviderQuoteRequest,
        results: list[QuoteResult],
        vault_resolution: QuoteVaultResolution | None,
    ) -> None:
        if vault_resolution is None:
            return

        input_context = vault_resolution.input_vault_context
        output_context = vault_resolution.output_vault_context
        output_assets_to_shares = vault_resolution.output_assets_to_shares
        missing_output_converter = output_context is not None and output_assets_to_shares is None
        if missing_output_converter:
            _LOGGER.error(
                "quote_use_underlying_missing_output_converter",
                extra={"chain_id": req.chain_id, "token_out": req.token_out.address},
            )

        for result in results:
            if result.status != ProviderStatus.OK:
                continue
            result.amount_in = req.amount_in
            if missing_output_converter:
                _mark_quote_conversion_failure(result)
                continue
            if output_context is not None:
                assert output_assets_to_shares is not None
                try:
                    if result.amount_out is not None:
                        result.amount_out = output_assets_to_shares(result.amount_out)
                    if result.amount_out_min is not None:
                        result.amount_out_min = output_assets_to_shares(result.amount_out_min)
                except Exception:
                    _LOGGER.warning(
                        "quote_use_underlying_output_conversion_failed",
                        extra={
                            "chain_id": req.chain_id,
                            "token_out": req.token_out.address,
                            "provider": result.provider,
                        },
                        exc_info=True,
                    )
                    _mark_quote_conversion_failure(result)
                    continue
            result.vault_context = _quote_vault_context(
                input_context=input_context,
                output_context=output_context,
            )

    def _sync_inflight(self, *, operation: str) -> None:
        set_admission_inflight_units(
            scope="vault",
            operation=operation,
            units=self._limiter.used,
        )


def _failed_price_resolution(
    *,
    req: ProviderPriceRequest,
    reason: str,
    started: float,
) -> ResolvedPriceRequest:
    return ResolvedPriceRequest(
        request=req,
        status=VaultResolutionStatus.FAILED,
        failure=VaultResolutionFailure(
            reason=reason,
            latency_ms=_elapsed_ms(started),
        ),
    )


def _failed_quote_resolution(
    *,
    req: ProviderQuoteRequest,
    reason: str,
    started: float,
) -> ResolvedQuoteRequest:
    return ResolvedQuoteRequest(
        request=req,
        status=VaultResolutionStatus.FAILED,
        failure=VaultResolutionFailure(
            reason=reason,
            latency_ms=_elapsed_ms(started),
        ),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _invalid_quote_resolution_reason(resolution: QuoteVaultResolution | None) -> str | None:
    if resolution is None:
        return "MISSING_VAULT_RESOLUTION"

    contexts = [
        resolution.input_vault_context,
        resolution.output_vault_context,
    ]
    if all(context is None for context in contexts):
        return "MISSING_VAULT_CONTEXT"
    if any(context is not None and context.price_per_share is None for context in contexts):
        return "INVALID_VAULT_RATE"
    if resolution.output_vault_context is not None and resolution.output_assets_to_shares is None:
        return "MISSING_VAULT_CONVERTER"
    return None


def _vault_share_to_asset_multiplier(price_per_share: Decimal | None) -> Decimal:
    if price_per_share is None or price_per_share <= 0:
        raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault price_per_share")
    return price_per_share


def _mark_quote_conversion_failure(result: QuoteResult) -> None:
    result.status = ProviderStatus.ERROR
    result.amount_out = None
    result.amount_out_min = None
    result.error = ErrorInfo(
        type=ErrorType.INVALID_VAULT_CONVERSION,
        message="Failed to convert output amount into vault share base units",
    )


def _quote_vault_context(
    *,
    input_context: VaultContext | None,
    output_context: VaultContext | None,
) -> VaultContext | None:
    if input_context is None and output_context is None:
        return None

    if input_context is not None and output_context is None:
        return input_context.model_copy(
            update={
                "underlying_token": None,
                "underlying_token_in": input_context.underlying_token,
                "underlying_token_out": None,
                "price_per_share": None,
                "price_per_share_token_in": input_context.price_per_share,
                "price_per_share_token_out": None,
            }
        )

    if input_context is None and output_context is not None:
        return output_context.model_copy(
            update={
                "underlying_token": None,
                "underlying_token_in": None,
                "underlying_token_out": output_context.underlying_token,
                "price_per_share": None,
                "price_per_share_token_in": None,
                "price_per_share_token_out": output_context.price_per_share,
            }
        )

    assert input_context is not None and output_context is not None
    return VaultContext(
        vault_type=input_context.vault_type
        if input_context.vault_type == output_context.vault_type
        else None,
        underlying_token=None,
        underlying_token_in=input_context.underlying_token,
        underlying_token_out=output_context.underlying_token,
        price_per_share=None,
        price_per_share_token_in=input_context.price_per_share,
        price_per_share_token_out=output_context.price_per_share,
        block_number=input_context.block_number,
    )

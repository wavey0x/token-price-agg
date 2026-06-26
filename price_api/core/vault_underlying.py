from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from price_api.app.config import Settings
from price_api.core.errors import ErrorCode, ErrorInfo, InvalidRequestError, ProviderStatus
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
    set_admission_inflight_units,
)
from price_api.vault.resolver import QuoteVaultResolution, VaultResolver

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedPriceRequest:
    request: ProviderPriceRequest
    vault_context: VaultContext | None = None


@dataclass(frozen=True, slots=True)
class ResolvedQuoteRequest:
    request: ProviderQuoteRequest
    vault_resolution: QuoteVaultResolution | None = None


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
            return ResolvedPriceRequest(request=req)

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
            return ResolvedPriceRequest(request=req)

        self._sync_inflight(operation="price")
        try:
            resolved_req, vault_context = await self._vault_resolver.resolve_price_request(req)
            return ResolvedPriceRequest(request=resolved_req, vault_context=vault_context)
        except InvalidRequestError as exc:
            _LOGGER.info(
                "price_use_underlying_resolution_skipped",
                extra={
                    "chain_id": req.chain_id,
                    "token": req.token.address,
                    "error_code": exc.code,
                },
            )
            return ResolvedPriceRequest(request=req)
        except Exception:
            _LOGGER.warning(
                "price_use_underlying_resolution_failed",
                extra={"chain_id": req.chain_id, "token": req.token.address},
                exc_info=True,
            )
            return ResolvedPriceRequest(request=req)
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
            return ResolvedQuoteRequest(request=req)

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
            return ResolvedQuoteRequest(request=req)

        self._sync_inflight(operation="quote")
        try:
            resolved_req, resolution = await self._vault_resolver.resolve_quote_request(req)
            return ResolvedQuoteRequest(request=resolved_req, vault_resolution=resolution)
        except InvalidRequestError as exc:
            _LOGGER.info(
                "quote_use_underlying_resolution_skipped",
                extra={
                    "chain_id": req.chain_id,
                    "token_in": req.token_in.address,
                    "token_out": req.token_out.address,
                    "error_code": exc.code,
                },
            )
            return ResolvedQuoteRequest(request=req)
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
            return ResolvedQuoteRequest(request=req)
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


def _vault_share_to_asset_multiplier(price_per_share: Decimal | None) -> Decimal:
    if price_per_share is None or price_per_share <= 0:
        raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault price_per_share")
    return price_per_share


def _mark_quote_conversion_failure(result: QuoteResult) -> None:
    result.status = ProviderStatus.ERROR
    result.amount_out = None
    result.amount_out_min = None
    result.error = ErrorInfo(
        code=ErrorCode.INVALID_VAULT_CONVERSION,
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

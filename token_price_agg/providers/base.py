from __future__ import annotations

from abc import ABC
from typing import ClassVar

from token_price_agg.core.errors import ErrorCode, ErrorInfo, ProviderStatus
from token_price_agg.core.models import (
    PriceResult,
    ProviderCapability,
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
)


class ProviderPlugin(ABC):
    id: ClassVar[str]
    supports_price: ClassVar[bool] = False
    supports_quote: ClassVar[bool] = False
    supported_chains: ClassVar[list[int]] = [1]
    requires_api_key: ClassVar[bool] = False

    def __init__(
        self,
        *,
        available: bool = True,
        unavailable_reason: str | None = None,
    ) -> None:
        self._available = available
        self._unavailable_reason = unavailable_reason

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            id=self.id,
            supports_price=self.supports_price,
            supports_quote=self.supports_quote,
            supported_chains=list(self.supported_chains),
            requires_api_key=self.requires_api_key,
            available=self.available,
            unavailable_reason=self.unavailable_reason,
        )

    async def get_price(self, req: ProviderPriceRequest) -> PriceResult:
        return PriceResult(
            provider=self.id,
            status=ProviderStatus.BAD_REQUEST,
            latency_ms=0,
            token=req.token,
            error=ErrorInfo(code=ErrorCode.UNSUPPORTED_OPERATION, message="Price not supported"),
        )

    async def get_quote(self, req: ProviderQuoteRequest) -> QuoteResult:
        return QuoteResult(
            provider=self.id,
            status=ProviderStatus.BAD_REQUEST,
            latency_ms=0,
            token_in=req.token_in,
            token_out=req.token_out,
            amount_in=req.amount_in,
            error=ErrorInfo(code=ErrorCode.UNSUPPORTED_OPERATION, message="Quote not supported"),
        )

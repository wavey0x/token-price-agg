from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from price_api.core.errors import ErrorInfo, ProviderStatus
from price_api.core.models import (
    AggregatePriceSummary,
    ProviderCapability,
    VaultType,
)


class BaseAggregateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    chain_id: int


class TokenMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    address: str
    symbol: str | None = None
    decimals: int | None = None
    logo_url: str | None = None


class PriceProviderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProviderStatus
    success: bool
    price: Decimal | None = None
    latency_ms: int
    as_of: datetime | None = None
    retrieved_at: datetime
    error: ErrorInfo | None = None


class SelectedPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    price: Decimal | None = None
    latency_ms: int
    as_of: datetime | None = None
    retrieved_at: datetime
    vault_context: PriceVaultContext | None = None


class QuoteProviderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProviderStatus
    success: bool
    amount_in: str | None = None
    amount_out: str | None = None
    amount_out_min: str | None = None
    price_impact_bps: int | None = None
    estimated_gas: int | None = None
    latency_ms: int
    as_of: datetime | None = None
    retrieved_at: datetime
    error: ErrorInfo | None = None
    route: dict[str, object] | None = None


class SelectedQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    amount_in: str | None = None
    amount_out: str | None = None
    amount_out_min: str | None = None
    price_impact_bps: int | None = None
    estimated_gas: int | None = None
    latency_ms: int
    as_of: datetime | None = None
    retrieved_at: datetime
    route: dict[str, object] | None = None
    vault_context: QuoteVaultContext | None = None


class PriceVaultContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vault_type: VaultType | None = None
    underlying_token: str | None = None
    price_per_share: Decimal
    block_number: int


class QuoteVaultContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vault_type: VaultType | None = None
    underlying_token_in: str | None = None
    underlying_token_out: str | None = None
    price_per_share_token_in: Decimal | None = None
    price_per_share_token_out: Decimal | None = None
    block_number: int


class AggregateQuoteSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_providers: int
    successful_providers: int
    failed_providers: int
    high_amount_out: str | None = None
    low_amount_out: str | None = None
    median_amount_out: str | None = None


class PriceAggregateResponse(BaseAggregateResponse):
    token: TokenMetadataResponse
    provider_order: list[str]
    price_data: SelectedPrice | None
    providers: dict[str, PriceProviderEntry]
    summary: AggregatePriceSummary


class QuoteAggregateResponse(BaseAggregateResponse):
    token_in: TokenMetadataResponse
    token_out: TokenMetadataResponse
    provider_order: list[str]
    quote: SelectedQuote | None
    providers: dict[str, QuoteProviderEntry]
    summary: AggregateQuoteSummaryResponse


class TokenResponse(BaseAggregateResponse):
    token: TokenMetadataResponse


class ProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCapability]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    checks: dict[str, bool | int | str]

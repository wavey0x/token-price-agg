from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from token_price_agg.core.errors import ErrorInfo, ProviderStatus
from token_price_agg.core.validator import AddressValidator


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TokenRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    address: str
    symbol: str | None = None
    decimals: int | None = None
    logo_url: str | None = None

    @field_validator("address")
    @classmethod
    def _normalize_address(cls, value: str) -> str:
        return AddressValidator.normalize_address(value)


class VaultType(str, Enum):
    ERC4626 = "erc4626"
    YEARN_V2 = "yearn_v2"


class VaultContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vault_type: VaultType
    underlying_token: str
    share_to_asset_rate: str
    block_number: int

    @field_validator("underlying_token")
    @classmethod
    def _normalize_underlying(cls, value: str) -> str:
        return AddressValidator.normalize_address(value)


class ProviderPriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    token: TokenRef


class ProviderQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    token_in: TokenRef
    token_out: TokenRef
    amount_in: int


class PriceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    status: ProviderStatus
    token: TokenRef | None = None
    price_usd: Decimal | None = None
    latency_ms: int
    as_of: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    error: ErrorInfo | None = None
    raw: dict[str, object] | None = None
    vault_context: VaultContext | None = None

    @computed_field(return_type=bool)  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return self.status == ProviderStatus.OK


class QuoteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    status: ProviderStatus
    token_in: TokenRef | None = None
    token_out: TokenRef | None = None
    amount_in: int | None = None
    amount_out: int | None = None
    amount_out_min: int | None = None
    price_impact_bps: int | None = None
    estimated_gas: int | None = None
    latency_ms: int
    as_of: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    error: ErrorInfo | None = None
    route: dict[str, object] | None = None
    vault_context: VaultContext | None = None

    @computed_field(return_type=bool)  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return self.status == ProviderStatus.OK


class ProviderCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    supports_price: bool
    supports_quote: bool
    supported_chains: list[int]
    requires_api_key: bool = False
    available: bool = True
    unavailable_reason: str | None = None


class TokenMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    address: str
    symbol: str | None = None
    decimals: int | None = None
    logo_url: str | None = None
    logo_status: str = "unknown"
    logo_checked_at: int | None = None
    logo_http_status: int | None = None
    source: str | None = None

    @field_validator("address")
    @classmethod
    def _normalize_address(cls, value: str) -> str:
        return AddressValidator.normalize_address(value)

    @field_validator("logo_status", mode="after")
    @classmethod
    def _validate_logo_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"unknown", "valid", "invalid"}:
            raise ValueError("logo_status must be one of: unknown, valid, invalid")
        return normalized


class AggregatePriceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_providers: int
    successful_providers: int
    failed_providers: int
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    median_price: Decimal | None = None
    deviation_bps: int | None = None


class AggregateQuoteSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_providers: int
    successful_providers: int
    failed_providers: int
    high_amount_out: int | None = None
    low_amount_out: int | None = None
    median_amount_out: int | None = None

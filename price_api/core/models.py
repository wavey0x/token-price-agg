from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from price_api.core.errors import ErrorInfo, ProviderStatus
from price_api.core.validator import MAX_UINT256, AddressValidator

MAX_NORMALIZED_DECIMAL_EXPONENT = 100


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TokenRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    address: str
    symbol: str | None = None
    decimals: int | None = Field(default=None, ge=0, le=255)
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

    vault_type: VaultType | None = None
    underlying_token: str | None = None
    underlying_token_in: str | None = None
    underlying_token_out: str | None = None
    price_per_share: Decimal | None = None
    price_per_share_token_in: Decimal | None = None
    price_per_share_token_out: Decimal | None = None
    block_number: int = Field(ge=0)

    @field_validator("underlying_token", "underlying_token_in", "underlying_token_out")
    @classmethod
    def _normalize_underlying(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return AddressValidator.normalize_address(value)

    @field_validator(
        "price_per_share",
        "price_per_share_token_in",
        "price_per_share_token_out",
    )
    @classmethod
    def _validate_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("vault conversion rate must be finite and positive")
        return value


class ProviderPriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    token: TokenRef
    timeout_ms: int | None = None


class ProviderQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    token_in: TokenRef
    token_out: TokenRef
    amount_in: int
    timeout_ms: int | None = None


class PriceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    status: ProviderStatus
    token: TokenRef | None = None
    price_usd: Decimal | None = None
    latency_ms: int = Field(ge=0)
    as_of: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    error: ErrorInfo | None = None
    raw: dict[str, object] | None = None
    vault_context: VaultContext | None = None

    @computed_field(return_type=bool)  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return self.status == ProviderStatus.OK

    @field_validator("price_usd")
    @classmethod
    def _validate_price(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (
            not value.is_finite()
            or value <= 0
            or abs(value.adjusted()) > MAX_NORMALIZED_DECIMAL_EXPONENT
        ):
            raise ValueError("price_usd must be finite, positive, and within numeric bounds")
        return value

    @model_validator(mode="after")
    def _validate_status_payload(self) -> PriceResult:
        if self.status == ProviderStatus.OK and self.price_usd is None:
            raise ValueError("successful price result requires price_usd")
        if self.status == ProviderStatus.OK and self.error is not None:
            raise ValueError("successful price result cannot contain an error")
        return self


class QuoteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    status: ProviderStatus
    token_in: TokenRef | None = None
    token_out: TokenRef | None = None
    amount_in: int | None = Field(default=None, ge=0, le=MAX_UINT256)
    amount_out: int | None = Field(default=None, ge=0, le=MAX_UINT256)
    amount_out_min: int | None = Field(default=None, ge=0, le=MAX_UINT256)
    price_impact_bps: int | None = None
    estimated_gas: int | None = Field(default=None, ge=0, le=MAX_UINT256)
    latency_ms: int = Field(ge=0)
    as_of: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    error: ErrorInfo | None = None
    route: dict[str, object] | None = None
    vault_context: VaultContext | None = None

    @computed_field(return_type=bool)  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return self.status == ProviderStatus.OK

    @model_validator(mode="after")
    def _validate_status_payload(self) -> QuoteResult:
        if self.status == ProviderStatus.OK and (self.amount_out is None or self.amount_out <= 0):
            raise ValueError("successful quote result requires a positive amount_out")
        if self.status == ProviderStatus.OK and self.error is not None:
            raise ValueError("successful quote result cannot contain an error")
        if (
            self.amount_out_min is not None
            and self.amount_out is not None
            and self.amount_out_min > self.amount_out
        ):
            raise ValueError("amount_out_min cannot exceed amount_out")
        return self


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
    logo_source: str | None = None
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

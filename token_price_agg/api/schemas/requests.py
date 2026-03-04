from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseAggregatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int = Field(gt=0)
    providers: list[str] | None = None
    is_vault: bool = False

    @field_validator("providers")
    @classmethod
    def _normalize_providers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        normalized: list[str] = []
        seen: set[str] = set()
        for provider in value:
            provider_id = provider.strip().lower()
            if not provider_id or provider_id in seen:
                continue
            normalized.append(provider_id)
            seen.add(provider_id)
        return normalized


class PriceRequest(BaseAggregatorRequest):
    token: str = Field(min_length=42)


class QuoteRequest(BaseAggregatorRequest):
    token_in: str = Field(min_length=42)
    token_out: str = Field(min_length=42)
    amount_in: str
    include_route: bool = False

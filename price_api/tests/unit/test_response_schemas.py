from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from price_api.api.schemas.responses import PriceProviderEntry, QuoteVaultContext
from price_api.core.errors import ErrorInfo, ErrorType, ProviderStatus
from price_api.core.models import PriceResult, QuoteResult, VaultContext, VaultType


def test_quote_vault_context_uses_leg_specific_price_per_share_fields() -> None:
    context = QuoteVaultContext(
        vault_type=VaultType.ERC4626,
        underlying_token_in="0xD533a949740bb3306d119CC777fa900bA034cd52",
        underlying_token_out=None,
        price_per_share_token_in=Decimal("1.459948592017731652"),
        price_per_share_token_out=None,
        block_number=21940623,
    )

    dumped = context.model_dump()
    assert dumped["price_per_share_token_in"] == Decimal("1.459948592017731652")
    assert "price_per_share_token_out" in dumped
    assert dumped["price_per_share_token_out"] is None
    assert "price_per_share" not in dumped


def test_quote_vault_context_rejects_legacy_price_per_share_field() -> None:
    with pytest.raises(ValidationError):
        QuoteVaultContext.model_validate(
            {
                "vault_type": "erc4626",
                "underlying_token_in": "0xD533a949740bb3306d119CC777fa900bA034cd52",
                "underlying_token_out": None,
                "price_per_share": "1.1",
                "block_number": 1,
            }
        )


def test_error_info_omits_unset_fields_without_dropping_provider_nulls() -> None:
    entry = PriceProviderEntry(
        status=ProviderStatus.ERROR,
        success=False,
        price=None,
        latency_ms=1,
        retrieved_at=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
        error=ErrorInfo(type=ErrorType.TIMEOUT, message="Provider request timed out"),
    )

    dumped = entry.model_dump(mode="json")

    assert dumped["error"] == {
        "type": "TIMEOUT",
        "message": "Provider request timed out",
    }
    assert "price" in dumped
    assert dumped["price"] is None
    assert "as_of" in dumped
    assert dumped["as_of"] is None


@pytest.mark.parametrize(
    "unsafe_price",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("1e101")],
)
def test_price_result_rejects_unsafe_success_values(unsafe_price: Decimal) -> None:
    with pytest.raises(ValidationError):
        PriceResult(
            provider="unsafe",
            status=ProviderStatus.OK,
            price_usd=unsafe_price,
            latency_ms=1,
        )


def test_quote_result_rejects_missing_or_inconsistent_success_amounts() -> None:
    with pytest.raises(ValidationError, match="positive amount_out"):
        QuoteResult(provider="unsafe", status=ProviderStatus.OK, amount_out=0, latency_ms=1)
    with pytest.raises(ValidationError, match="cannot exceed"):
        QuoteResult(
            provider="unsafe",
            status=ProviderStatus.OK,
            amount_out=10,
            amount_out_min=11,
            latency_ms=1,
        )
    with pytest.raises(ValidationError):
        QuoteResult(
            provider="unsafe",
            status=ProviderStatus.OK,
            amount_out=2**256,
            latency_ms=1,
        )


def test_vault_context_rejects_unsafe_rate_and_block() -> None:
    with pytest.raises(ValidationError):
        VaultContext(block_number=-1, price_per_share=Decimal("1"))
    with pytest.raises(ValidationError):
        VaultContext(block_number=1, price_per_share=Decimal("Infinity"))


def test_normal_provider_and_vault_values_remain_valid() -> None:
    price = PriceResult(
        provider="safe",
        status=ProviderStatus.OK,
        price_usd=Decimal("1.25"),
        latency_ms=1,
    )
    quote = QuoteResult(
        provider="safe",
        status=ProviderStatus.OK,
        amount_out=10,
        amount_out_min=9,
        latency_ms=1,
    )
    vault = VaultContext(block_number=1, price_per_share=Decimal("1.1"))

    assert price.success and quote.success and vault.price_per_share == Decimal("1.1")

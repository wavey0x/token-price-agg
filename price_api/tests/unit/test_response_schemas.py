from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from price_api.api.schemas.responses import PriceProviderEntry, QuoteVaultContext
from price_api.core.errors import ErrorInfo, ErrorType, ProviderStatus
from price_api.core.models import VaultType


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

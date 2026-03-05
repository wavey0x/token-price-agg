from decimal import Decimal

import pytest
from pydantic import ValidationError

from token_price_agg.api.schemas.responses import QuoteVaultContext
from token_price_agg.core.models import VaultType


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

from __future__ import annotations

import pytest
from pydantic import ValidationError

from token_price_agg.api.schemas.requests import PriceRequest, QuoteRequest

TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_is_vault_defaults_false_for_price() -> None:
    req = PriceRequest(chain_id=1, token=TOKEN)
    assert req.is_vault is False


def test_is_vault_defaults_false_for_quote() -> None:
    req = QuoteRequest(chain_id=1, token_in=TOKEN, token_out=TOKEN, amount_in="1")
    assert req.is_vault is False


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        PriceRequest.model_validate({"chain_id": 1, "token": TOKEN, "unknown": "x"})


def test_price_amount_removed_and_forbidden() -> None:
    with pytest.raises(ValidationError):
        PriceRequest.model_validate({"chain_id": 1, "token": TOKEN, "amount": "1000000"})


def test_providers_are_normalized_to_lowercase() -> None:
    req = PriceRequest(chain_id=1, token=TOKEN, providers=["CurVe", "CURVE", "defiLlama"])
    assert req.providers == ["curve", "defillama"]

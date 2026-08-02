from __future__ import annotations

import pytest

from price_api.tests.fixtures.ethereum_tokens import MAINNET_TOKENS
from price_api.tests.manual.smoke_get_live import (
    _validate_price_response,
    _validate_quote_response,
)


def test_live_smoke_requires_a_selected_success() -> None:
    price_failure = {
        "token": {"address": MAINNET_TOKENS["USDC"]},
        "provider_order": ["curve"],
        "price_data": None,
        "providers": {"curve": {"success": False}},
    }
    quote_failure = {
        "token_out": {"address": MAINNET_TOKENS["USDC"]},
        "provider_order": ["curve"],
        "quote": None,
        "providers": {"curve": {"success": False}},
    }

    with pytest.raises(AssertionError):
        _validate_price_response("USDC", price_failure)
    with pytest.raises(AssertionError):
        _validate_quote_response("USDC", quote_failure)


def test_live_smoke_accepts_success_with_string_quote_amounts() -> None:
    price_success = {
        "token": {"address": MAINNET_TOKENS["USDC"]},
        "provider_order": ["curve"],
        "price_data": {"provider": "curve"},
        "providers": {"curve": {"success": True}},
    }
    quote_success = {
        "token_out": {"address": MAINNET_TOKENS["USDC"]},
        "provider_order": ["curve"],
        "quote": {
            "provider": "curve",
            "amount_in": "1000000000000000000",
            "amount_out": "1000000",
            "amount_out_min": None,
        },
        "providers": {"curve": {"success": True}},
    }

    _validate_price_response("USDC", price_success)
    _validate_quote_response("USDC", quote_success)

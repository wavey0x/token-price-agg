from __future__ import annotations

import pytest
from eth_utils.address import to_checksum_address

from token_price_agg.core.errors import InvalidRequestError
from token_price_agg.core.validator import NATIVE_TOKEN_ALIAS, AddressValidator, parse_positive_int


@pytest.mark.parametrize(
    "input_address",
    [
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    ],
)
def test_normalize_address_case_insensitive(input_address: str) -> None:
    normalized = AddressValidator.normalize_address(input_address)
    assert normalized == to_checksum_address(input_address)


def test_normalize_native_alias() -> None:
    normalized = AddressValidator.normalize_address("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    assert normalized == NATIVE_TOKEN_ALIAS


def test_parse_positive_int() -> None:
    assert parse_positive_int("123", "amount") == 123


@pytest.mark.parametrize("invalid", ["0", "-1", "abc", "1.5", ""])
def test_parse_positive_int_invalid(invalid: str) -> None:
    with pytest.raises(InvalidRequestError):
        parse_positive_int(invalid, "amount")

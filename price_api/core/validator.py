from __future__ import annotations

import re

from eth_utils.address import is_address, to_checksum_address

from price_api.core.errors import InvalidRequestError

NATIVE_TOKEN_ALIAS = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")
MAX_POSITIVE_INT_DIGITS = 78
MAX_UINT256 = 2**256 - 1


class AddressValidator:
    @staticmethod
    def is_native_alias(address: str) -> bool:
        lowered = address.lower()
        return lowered == NATIVE_TOKEN_ALIAS.lower() or lowered == ZERO_ADDRESS.lower()

    @classmethod
    def normalize_address(cls, address: str) -> str:
        if cls.is_native_alias(address):
            return NATIVE_TOKEN_ALIAS

        if not is_address(address):
            raise InvalidRequestError("INVALID_ADDRESS", f"Invalid EVM address: {address}")

        return to_checksum_address(address)


def parse_positive_int(value: str, field_name: str) -> int:
    if len(value) > MAX_POSITIVE_INT_DIGITS or not _POSITIVE_INT_RE.match(value):
        raise InvalidRequestError(
            "INVALID_AMOUNT", f"Field '{field_name}' must be a positive integer string"
        )
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InvalidRequestError(
            "INVALID_AMOUNT", f"Field '{field_name}' must be a positive integer string"
        ) from exc
    if parsed > MAX_UINT256:
        raise InvalidRequestError(
            "INVALID_AMOUNT", f"Field '{field_name}' exceeds the uint256 maximum"
        )
    return parsed

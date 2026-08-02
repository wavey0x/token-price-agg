from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ABI_DIR = Path(__file__).resolve().parents[1] / "abi"
MAX_SAFE_TOKEN_DECIMALS = 77


def load_abi(name: str) -> list[dict[str, Any]]:
    with (_ABI_DIR / name).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"ABI file {name} must contain a list")
    return data


def decode_token_decimals(data: bytes) -> int | None:
    """Decode the canonical ABI encoding of a safe ERC-20 decimals value."""
    if len(data) != 32 or any(data[:-1]):
        return None
    value = data[-1]
    return value if value <= MAX_SAFE_TOKEN_DECIMALS else None


def validate_token_decimals(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_SAFE_TOKEN_DECIMALS else None

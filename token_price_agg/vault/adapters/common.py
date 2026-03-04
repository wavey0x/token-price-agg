from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ABI_DIR = Path(__file__).resolve().parents[1] / "abi"


def load_abi(name: str) -> list[dict[str, Any]]:
    with (_ABI_DIR / name).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"ABI file {name} must contain a list")
    return data

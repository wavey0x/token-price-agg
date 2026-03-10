from __future__ import annotations

_REMAP_TABLE: dict[tuple[int, str], str] = {
    (1, "0xa3cc91589feedbbee0cfdc7404041e19cb00f110"): "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B",
}


def resolve_remap(chain_id: int, address: str) -> str | None:
    return _REMAP_TABLE.get((chain_id, address.lower()))

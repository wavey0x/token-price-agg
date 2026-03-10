from __future__ import annotations

from token_price_agg.core.address_remap import resolve_remap


def test_resolve_remap_returns_canonical_for_known_pair() -> None:
    result = resolve_remap(1, "0xa3cc91589feedbbee0cfdc7404041e19cb00f110")
    assert result == "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B"


def test_resolve_remap_returns_none_for_unknown_address() -> None:
    result = resolve_remap(1, "0x0000000000000000000000000000000000000001")
    assert result is None


def test_resolve_remap_returns_none_for_wrong_chain() -> None:
    result = resolve_remap(137, "0xa3cc91589feedbbee0cfdc7404041e19cb00f110")
    assert result is None


def test_resolve_remap_case_insensitive() -> None:
    result = resolve_remap(1, "0xA3CC91589FEeDBBEe0cFDc7404041e19cb00f110")
    assert result == "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B"

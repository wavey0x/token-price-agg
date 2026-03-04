from __future__ import annotations

from token_price_agg.api.schemas.query_params import parse_provider_query_values


def test_parse_provider_query_values_repeated() -> None:
    assert parse_provider_query_values(["curve", "defillama"]) == ["curve", "defillama"]


def test_parse_provider_query_values_csv() -> None:
    assert parse_provider_query_values(["curve,defillama"]) == ["curve", "defillama"]


def test_parse_provider_query_values_mixed() -> None:
    assert parse_provider_query_values(["curve,defillama", "enso"]) == [
        "curve",
        "defillama",
        "enso",
    ]


def test_parse_provider_query_values_empty_items() -> None:
    assert parse_provider_query_values(["curve, ,", "   ", "defillama"]) == ["curve", "defillama"]


def test_parse_provider_query_values_none() -> None:
    assert parse_provider_query_values(None) is None

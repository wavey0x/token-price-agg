from __future__ import annotations

from token_price_agg.providers.parsing import parse_base_unit_amount, parse_datetime


def test_parse_datetime_seconds_timestamp() -> None:
    parsed = parse_datetime(1_700_000_000)
    assert parsed is not None
    assert parsed.year == 2023


def test_parse_datetime_milliseconds_timestamp() -> None:
    parsed = parse_datetime(1_772_636_791_070)
    assert parsed is not None
    assert parsed.year == 2026


def test_parse_datetime_microseconds_timestamp() -> None:
    parsed = parse_datetime(1_772_636_791_070_000)
    assert parsed is not None
    assert parsed.year == 2026


def test_parse_datetime_invalid_huge_timestamp_returns_none() -> None:
    parsed = parse_datetime(10**30)
    assert parsed is None


def test_parse_base_unit_amount_integer_passthrough() -> None:
    assert parse_base_unit_amount("2125893537", token_decimals=6) == 2125893537


def test_parse_base_unit_amount_human_decimal_to_base_units() -> None:
    assert parse_base_unit_amount("2125.893537", token_decimals=6) == 2125893537

from __future__ import annotations

from decimal import Decimal

from price_api.providers.parsing import (
    parse_base_unit_amount,
    parse_datetime,
    parse_decimal,
    parse_int,
    parse_positive_decimal,
)


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


def test_external_numbers_reject_non_finite_negative_and_extreme_values() -> None:
    assert parse_decimal("NaN") is None
    assert parse_decimal("Infinity") is None
    assert parse_decimal("1e1000") is None
    assert parse_decimal("1" * 101) is None
    assert parse_positive_decimal("0") is None
    assert parse_positive_decimal("-1") is None
    assert parse_int(-1) is None
    assert parse_int("1" * 5000) is None
    assert parse_base_unit_amount(str(2**256), token_decimals=0) is None
    assert parse_decimal(10**5000) is None


def test_external_numbers_preserve_normal_positive_values() -> None:
    assert parse_positive_decimal("123.45") == Decimal("123.45")
    assert parse_int("123") == 123

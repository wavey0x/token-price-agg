from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from token_price_agg.core.models import TokenRef

_MILLISECONDS_CUTOFF = 1_000_000_000_000
_MICROSECONDS_CUTOFF = 1_000_000_000_000_000
_NANOSECONDS_CUTOFF = 1_000_000_000_000_000_000


def parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def parse_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def parse_base_unit_amount(value: object, *, token_decimals: int | None) -> int | None:
    """Parse a token amount into base units (wei-style integer).

    Accepts:
    - integer/base-unit values directly
    - decimal human-unit values when token_decimals is available
    """
    parsed_int = parse_int(value)
    if parsed_int is not None:
        return parsed_int

    parsed_decimal = parse_decimal(value)
    if parsed_decimal is None or parsed_decimal < 0:
        return None

    if parsed_decimal == parsed_decimal.to_integral_value():
        return int(parsed_decimal)

    if token_decimals is None or token_decimals < 0:
        return None

    scaled = parsed_decimal * (Decimal(10) ** token_decimals)
    return int(scaled.to_integral_value(rounding=ROUND_DOWN))


def parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, int):
        return _from_unix_timestamp(value)
    if isinstance(value, float):
        return _from_unix_timestamp(value)
    if isinstance(value, str):
        if value.isdigit():
            return _from_unix_timestamp(int(value))
        try:
            # Accept ISO8601 with or without trailing Z.
            iso_value = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(iso_value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _from_unix_timestamp(value: int | float) -> datetime | None:
    candidate = float(value)
    absolute = abs(candidate)

    if absolute >= _NANOSECONDS_CUTOFF:
        candidate /= 1_000_000_000
    elif absolute >= _MICROSECONDS_CUTOFF:
        candidate /= 1_000_000
    elif absolute >= _MILLISECONDS_CUTOFF:
        candidate /= 1_000

    try:
        return datetime.fromtimestamp(candidate, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def parse_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def get_first(dct: dict[str, object], keys: list[str]) -> object | None:
    for key in keys:
        if key in dct:
            return dct[key]
    return None


def get_nested(dct: dict[str, object], path: list[str]) -> object | None:
    current: object = dct
    for key in path:
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current[key]
    return current


def parse_token_metadata_fields(
    payload: dict[str, object],
) -> tuple[str | None, int | None, str | None]:
    symbol = parse_str(get_first(payload, ["symbol", "ticker"]))

    decimals_raw = get_first(payload, ["decimals", "tokenDecimals", "precision"])
    decimals = parse_int(decimals_raw)
    if decimals is not None and (decimals < 0 or decimals > 255):
        decimals = None

    logo_url = parse_str(
        get_first(
            payload,
            ["logoURI", "logoUri", "logo_url", "logoUrl", "logo", "icon", "iconUrl", "image"],
        )
    )

    return symbol, decimals, logo_url


def with_token_metadata(base: TokenRef, payload: object) -> TokenRef:
    if not isinstance(payload, dict):
        return base
    symbol, decimals, logo_url = parse_token_metadata_fields(payload)
    return base.model_copy(
        update={
            "symbol": symbol or base.symbol,
            "decimals": decimals if decimals is not None else base.decimals,
            "logo_url": logo_url or base.logo_url,
        }
    )


def decimal_to_bps(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * Decimal(10000)).to_integral_value())

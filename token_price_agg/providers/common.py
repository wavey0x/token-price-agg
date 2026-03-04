from __future__ import annotations

from collections.abc import Sequence

from token_price_agg.providers.parsing import get_nested


def payload_data_or_root(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def first_nested_dict(
    payload: dict[str, object],
    *,
    paths: Sequence[Sequence[str]],
) -> dict[str, object] | None:
    for path in paths:
        value = get_nested(payload, list(path))
        if isinstance(value, dict):
            return value
    return None

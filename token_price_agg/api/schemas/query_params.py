from __future__ import annotations


def parse_provider_query_values(values: list[str] | None) -> list[str] | None:
    if not values:
        return None

    flattened: list[str] = []
    for item in values:
        for part in item.split(","):
            provider = part.strip()
            if provider:
                flattened.append(provider)

    return flattened or None

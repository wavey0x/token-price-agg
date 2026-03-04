from __future__ import annotations

from token_price_agg.core.selection import build_provider_order


def test_default_priority_subset_appends_remaining_enabled_in_alphabetical_order() -> None:
    order = build_provider_order(
        available_provider_ids=["enso", "curve", "lifi", "defillama"],
        requested_provider_ids=None,
        default_priority=["curve", "defillama"],
    )
    assert order == ["curve", "defillama", "enso", "lifi"]


def test_priority_entries_not_available_are_ignored() -> None:
    order = build_provider_order(
        available_provider_ids=["curve", "defillama"],
        requested_provider_ids=None,
        default_priority=["curve", "lifi", "unknown", "defillama"],
    )
    assert order == ["curve", "defillama"]

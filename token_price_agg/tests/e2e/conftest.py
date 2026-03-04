from __future__ import annotations

import pytest

from token_price_agg.tests.e2e.helpers import clear_singletons


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    clear_singletons()

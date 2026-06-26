from __future__ import annotations

import pytest

from price_api.tests.e2e.helpers import clear_singletons


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    clear_singletons()

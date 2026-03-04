from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

LINE_BUDGETS = {
    "token_price_agg/core/aggregator.py": 160,
    "token_price_agg/token_metadata/resolver.py": 180,
    "token_price_agg/api/routes/prices.py": 180,
    "token_price_agg/api/routes/quotes.py": 180,
    "token_price_agg/tests/e2e/test_price_endpoints.py": 300,
    "token_price_agg/tests/e2e/test_quote_endpoints.py": 300,
    "token_price_agg/tests/e2e/test_operational_endpoints.py": 200,
    "token_price_agg/tests/e2e/test_security_endpoints.py": 200,
}


@pytest.mark.parametrize("relative_path,budget", LINE_BUDGETS.items())
def test_file_line_budget(relative_path: str, budget: int) -> None:
    path = REPO_ROOT / relative_path
    actual = sum(1 for _ in path.open("r", encoding="utf-8"))
    assert actual <= budget, f"{relative_path} has {actual} lines, budget is {budget}"

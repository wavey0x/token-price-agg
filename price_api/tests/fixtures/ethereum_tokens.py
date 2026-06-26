from __future__ import annotations

MAINNET_TOKENS: dict[str, str] = {
    "CRV": "0xD533a949740bb3306d119CC777fa900bA034cd52",
    "CVX": "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B",
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "YFI": "0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e",
}

DEFAULT_PRICE_SYMBOLS: list[str] = ["CRV", "CVX", "USDC", "YFI"]
DEFAULT_QUOTE_SYMBOLS: list[str] = ["CRV", "CVX", "USDC", "YFI"]


def build_directed_quote_pairs(symbols: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for token_in in symbols:
        for token_out in symbols:
            if token_in == token_out:
                continue
            pairs.append((token_in, token_out))
    return pairs

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx
from eth_utils.address import to_checksum_address

from token_price_agg.tests.fixtures.ethereum_tokens import (
    DEFAULT_PRICE_SYMBOLS,
    DEFAULT_QUOTE_SYMBOLS,
    MAINNET_TOKENS,
    build_directed_quote_pairs,
)


def _parse_symbols(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    items = [item.strip().upper() for item in value.split(",")]
    return [item for item in items if item]


def _params_with_providers(
    base: dict[str, str],
    providers: list[str] | None,
) -> list[tuple[str, str | int | float | bool | None]]:
    params: list[tuple[str, str | int | float | bool | None]] = list(base.items())
    if providers is None:
        return params
    for provider in providers:
        params.append(("providers", provider))
    return params


def _validate_price_response(symbol: str, payload: dict[str, Any]) -> None:
    token = MAINNET_TOKENS[symbol]
    assert payload["token"]["address"] == token
    assert "query_type" not in payload
    assert "partial" not in payload
    assert "price_data" in payload
    providers = payload["providers"]
    assert isinstance(providers, dict)
    assert payload["provider_order"]


def _validate_quote_response(symbol_out: str, payload: dict[str, Any]) -> None:
    token_out = MAINNET_TOKENS[symbol_out]
    assert payload["token_out"]["address"] == token_out
    assert "query_type" not in payload
    assert "partial" not in payload
    providers = payload["providers"]
    assert isinstance(providers, dict)
    assert payload["provider_order"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke test for GET /v1/price and /v1/quote")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--chain-id", default=1, type=int)
    parser.add_argument("--amount-in", default="1000000000000000000")
    parser.add_argument(
        "--price-symbols",
        default=None,
        help="Comma-separated symbols to test for /v1/price (default: fixture defaults)",
    )
    parser.add_argument(
        "--quote-symbols",
        default=None,
        help="Comma-separated symbols to build directed quote pairs (default: fixture defaults)",
    )
    parser.add_argument(
        "--providers",
        default=None,
        help="Optional comma-separated provider IDs appended as repeated providers params",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional consumer API key used as Authorization: Bearer <key>",
    )
    args = parser.parse_args()

    price_symbols = _parse_symbols(args.price_symbols, DEFAULT_PRICE_SYMBOLS)
    quote_symbols = _parse_symbols(args.quote_symbols, DEFAULT_QUOTE_SYMBOLS)
    providers = _parse_symbols(args.providers, []) if args.providers else None

    for symbol in [*price_symbols, *quote_symbols]:
        if symbol not in MAINNET_TOKENS:
            print(f"unknown symbol: {symbol}", file=sys.stderr)
            return 2

    failures = 0
    total = 0
    timeout = httpx.Timeout(20.0)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else None

    with httpx.Client(base_url=args.base_url, timeout=timeout, headers=headers) as client:
        for symbol in price_symbols:
            total += 1
            token = MAINNET_TOKENS[symbol]
            params = _params_with_providers(
                base={
                    "chain_id": str(args.chain_id),
                    "token": token.lower(),
                },
                providers=providers,
            )
            response = client.get("/v1/price", params=params)
            if response.status_code != 200:
                failures += 1
                print(f"[FAIL] price {symbol}: status={response.status_code} body={response.text}")
                continue

            payload = response.json()
            try:
                _validate_price_response(symbol, payload)
            except Exception as exc:
                failures += 1
                print(f"[FAIL] price {symbol}: validation_error={type(exc).__name__}: {exc}")
                continue

            providers_payload = payload.get("providers", {})
            print(f"[PASS] price {symbol}: providers={len(providers_payload)}")

        for symbol_in, symbol_out in build_directed_quote_pairs(quote_symbols):
            total += 1
            token_in = to_checksum_address(MAINNET_TOKENS[symbol_in]).lower()
            token_out = to_checksum_address(MAINNET_TOKENS[symbol_out]).lower()
            params = _params_with_providers(
                base={
                    "chain_id": str(args.chain_id),
                    "token_in": token_in,
                    "token_out": token_out,
                    "amount_in": args.amount_in,
                },
                providers=providers,
            )
            response = client.get("/v1/quote", params=params)
            if response.status_code != 200:
                failures += 1
                print(
                    f"[FAIL] quote {symbol_in}->{symbol_out}: "
                    f"status={response.status_code} body={response.text}"
                )
                continue

            payload = response.json()
            try:
                _validate_quote_response(symbol_out, payload)
            except Exception as exc:
                failures += 1
                print(
                    f"[FAIL] quote {symbol_in}->{symbol_out}: "
                    f"validation_error={type(exc).__name__}: {exc}"
                )
                continue

            providers_payload = payload.get("providers", {})
            print(f"[PASS] quote {symbol_in}->{symbol_out}: providers={len(providers_payload)}")

    print(f"completed total={total} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

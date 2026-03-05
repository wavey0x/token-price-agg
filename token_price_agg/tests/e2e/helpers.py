from __future__ import annotations

import respx
from httpx import Response

from token_price_agg.app.dependencies import (
    get_aggregator_service,
    get_anonymous_rate_limiter,
    get_api_key_store,
    get_provider_registry,
    get_token_metadata_resolver,
    get_vault_resolver,
)
from token_price_agg.tests.fixtures.ethereum_tokens import (
    DEFAULT_QUOTE_SYMBOLS,
    MAINNET_TOKENS,
    build_directed_quote_pairs,
)

QUOTE_PAIRS = build_directed_quote_pairs(DEFAULT_QUOTE_SYMBOLS)


def clear_singletons() -> None:
    from token_price_agg.app.config import get_settings

    get_settings.cache_clear()
    get_api_key_store.cache_clear()
    get_anonymous_rate_limiter.cache_clear()
    get_provider_registry.cache_clear()
    get_vault_resolver.cache_clear()
    get_aggregator_service.cache_clear()
    get_token_metadata_resolver.cache_clear()


def token(symbol: str) -> str:
    return MAINNET_TOKENS[symbol]


def token_lower(symbol: str) -> str:
    return token(symbol).lower()


def mock_defillama_price(router: respx.MockRouter, token_address: str, symbol: str) -> None:
    coin_key = f"ethereum:{token_address}"
    router.get(f"https://coins.llama.fi/prices/current/{coin_key}").mock(
        return_value=Response(
            200,
            json={
                "coins": {
                    coin_key: {
                        "price": "1.00",
                        "timestamp": 1700000000,
                        "symbol": symbol,
                        "decimals": 18 if symbol != "USDC" else 6,
                    }
                }
            },
        )
    )


def issue_test_api_key(label: str = "e2e") -> str:
    store = get_api_key_store()
    issued = store.issue_key(label=label)
    return issued.key

import pytest

from price_api.core.models import ProviderPriceRequest, ProviderQuoteRequest, TokenRef
from price_api.core.vault_underlying import (
    ResolvedPriceRequest,
    ResolvedQuoteRequest,
    VaultResolutionStatus,
)

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
CRV = "0xD533a949740bb3306d119CC777fa900bA034cd52"


def test_price_resolution_status_requires_matching_payload() -> None:
    request = ProviderPriceRequest(chain_id=1, token=TokenRef(chain_id=1, address=USDC))

    with pytest.raises(ValueError, match="resolved price status"):
        ResolvedPriceRequest(request=request, status=VaultResolutionStatus.RESOLVED)
    with pytest.raises(ValueError, match="failed price status"):
        ResolvedPriceRequest(request=request, status=VaultResolutionStatus.FAILED)


def test_quote_resolution_status_requires_matching_payload() -> None:
    request = ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address=CRV),
        token_out=TokenRef(chain_id=1, address=USDC),
        amount_in=10**18,
    )

    with pytest.raises(ValueError, match="resolved quote status"):
        ResolvedQuoteRequest(request=request, status=VaultResolutionStatus.RESOLVED)
    with pytest.raises(ValueError, match="failed quote status"):
        ResolvedQuoteRequest(request=request, status=VaultResolutionStatus.FAILED)

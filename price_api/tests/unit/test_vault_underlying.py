from collections.abc import Callable
from decimal import Decimal

import pytest

from price_api.core.errors import ErrorType, ProviderStatus
from price_api.core.models import (
    ProviderPriceRequest,
    ProviderQuoteRequest,
    QuoteResult,
    TokenRef,
    VaultContext,
    VaultType,
)
from price_api.core.validator import MAX_UINT256
from price_api.core.vault_underlying import (
    ResolvedPriceRequest,
    ResolvedQuoteRequest,
    VaultResolutionStatus,
    VaultUnderlyingService,
)
from price_api.vault.resolver import QuoteVaultResolution

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


def test_quote_output_conversion_zero_fails_only_affected_provider() -> None:
    request = _quote_request()
    rounded_to_zero = _quote_result(provider="rounded", request=request, amount_out=1)
    valid = _quote_result(
        provider="valid",
        request=request,
        amount_out=4,
        amount_out_min=2,
    )

    VaultUnderlyingService.apply_quote_resolution(
        req=request,
        results=[rounded_to_zero, valid],
        vault_resolution=_output_vault_resolution(lambda assets: assets // 2),
    )

    assert rounded_to_zero.status == ProviderStatus.ERROR
    assert rounded_to_zero.amount_out is None
    assert rounded_to_zero.amount_out_min is None
    assert rounded_to_zero.error is not None
    assert rounded_to_zero.error.type == ErrorType.INVALID_VAULT_CONVERSION
    assert valid.status == ProviderStatus.OK
    assert valid.amount_out == 2
    assert valid.amount_out_min == 1


@pytest.mark.parametrize(
    ("converted_amount_out", "converted_amount_out_min"),
    [
        (MAX_UINT256 + 1, 1),
        (2, 3),
    ],
)
def test_quote_output_conversion_rejects_invalid_converted_amounts(
    converted_amount_out: int,
    converted_amount_out_min: int,
) -> None:
    request = _quote_request()
    result = _quote_result(
        provider="invalid",
        request=request,
        amount_out=4,
        amount_out_min=2,
    )
    converted = iter([converted_amount_out, converted_amount_out_min])

    VaultUnderlyingService.apply_quote_resolution(
        req=request,
        results=[result],
        vault_resolution=_output_vault_resolution(lambda _: next(converted)),
    )

    assert result.status == ProviderStatus.ERROR
    assert result.amount_out is None
    assert result.amount_out_min is None
    assert result.error is not None
    assert result.error.type == ErrorType.INVALID_VAULT_CONVERSION


def _quote_request() -> ProviderQuoteRequest:
    return ProviderQuoteRequest(
        chain_id=1,
        token_in=TokenRef(chain_id=1, address=CRV),
        token_out=TokenRef(chain_id=1, address=USDC),
        amount_in=10**18,
    )


def _quote_result(
    *,
    provider: str,
    request: ProviderQuoteRequest,
    amount_out: int,
    amount_out_min: int | None = None,
) -> QuoteResult:
    return QuoteResult(
        provider=provider,
        status=ProviderStatus.OK,
        token_in=request.token_in,
        token_out=request.token_out,
        amount_in=request.amount_in,
        amount_out=amount_out,
        amount_out_min=amount_out_min,
        latency_ms=1,
    )


def _output_vault_resolution(
    converter: Callable[[int], int],
) -> QuoteVaultResolution:
    return QuoteVaultResolution(
        output_vault_context=VaultContext(
            vault_type=VaultType.ERC4626,
            underlying_token=USDC,
            price_per_share=Decimal("1"),
            block_number=1,
        ),
        output_assets_to_shares=converter,
    )

from __future__ import annotations

from price_api.core.validator import AddressValidator

TOKEN_LOGO_ORIGIN = "https://prices.wavey.info"


def token_logo_url(*, chain_id: int, address: str) -> str:
    """Return the public resource identifier without probing for availability."""
    if chain_id <= 0:
        raise ValueError("chain_id must be positive")
    normalized = AddressValidator.normalize_address(address)
    return f"{TOKEN_LOGO_ORIGIN}/token-logos/{chain_id}/{normalized.lower()}"

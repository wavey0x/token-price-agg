from __future__ import annotations

from typing import Any

from price_api.core.models import TokenMetadata, TokenRef
from price_api.core.validator import AddressValidator
from price_api.token_metadata.logo_urls import token_logo_url


def hints_from_refs(refs: list[TokenRef], *, chain_id: int) -> dict[str, TokenMetadata]:
    """Collect metadata hints while deliberately discarding provider image URLs."""
    out: dict[str, TokenMetadata] = {}
    for ref in refs:
        address = AddressValidator.normalize_address(ref.address)
        hint = TokenMetadata(
            chain_id=chain_id,
            address=address,
            symbol=ref.symbol,
            decimals=ref.decimals,
            logo_url=None,
            source="provider",
        )
        out[address] = merge_metadata(
            chain_id=chain_id,
            address=address,
            cached=out.get(address),
            hint=hint,
            default_source="provider",
        )
    return out


def merge_metadata(
    *,
    chain_id: int,
    address: str,
    cached: TokenMetadata | None,
    hint: TokenMetadata | None,
    default_source: str,
) -> TokenMetadata:
    normalized = AddressValidator.normalize_address(address)
    native = _is_native(address=normalized, cached=cached, hint=hint)
    symbol = _pick_first(
        cached.symbol if cached is not None else None,
        hint.symbol if hint is not None else None,
        "ETH" if native and chain_id == 1 else None,
    )
    decimals = _pick_first(
        cached.decimals if cached is not None else None,
        hint.decimals if hint is not None else None,
        18 if native else None,
    )
    source = _pick_first(
        cached.source if cached is not None else None,
        hint.source if hint is not None else None,
        default_source,
    )

    return TokenMetadata(
        chain_id=chain_id,
        address=normalized,
        symbol=symbol,
        decimals=decimals,
        logo_url=token_logo_url(chain_id=chain_id, address=normalized),
        source=source,
    )


def _is_native(*, address: str, cached: TokenMetadata | None, hint: TokenMetadata | None) -> bool:
    if cached is not None and AddressValidator.is_native_alias(cached.address):
        return True
    if hint is not None and AddressValidator.is_native_alias(hint.address):
        return True
    return AddressValidator.is_native_alias(address)


def _pick_first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None

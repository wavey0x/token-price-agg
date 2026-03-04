from __future__ import annotations

from typing import Any

from token_price_agg.core.models import TokenMetadata, TokenRef
from token_price_agg.core.validator import NATIVE_TOKEN_ALIAS
from token_price_agg.token_metadata.logo_urls import build_logo_candidates


def hints_from_refs(refs: list[TokenRef], *, chain_id: int) -> dict[str, TokenMetadata]:
    out: dict[str, TokenMetadata] = {}
    for ref in refs:
        native = ref.is_native or ref.address.lower() == NATIVE_TOKEN_ALIAS.lower()
        hint = TokenMetadata(
            chain_id=chain_id,
            address=ref.address,
            is_native=native,
            symbol=ref.symbol,
            decimals=ref.decimals,
            logo_url=ref.logo_url,
            source="provider",
        )
        out[ref.address] = merge_metadata(
            chain_id=chain_id,
            address=ref.address,
            cached=out.get(ref.address),
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
    native = _is_native(address=address, cached=cached, hint=hint)
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
    logo_url = _pick_first(
        cached.logo_url if cached is not None else None,
        hint.logo_url if hint is not None else None,
    )
    source = _pick_first(
        cached.source if cached is not None else None,
        hint.source if hint is not None else None,
        default_source,
    )

    return TokenMetadata(
        chain_id=chain_id,
        address=address,
        is_native=native,
        symbol=symbol,
        decimals=decimals,
        logo_url=logo_url,
        source=source,
    )


def resolve_logo_for_response(
    *,
    chain_id: int,
    address: str,
    metadata: TokenMetadata,
    cached: TokenMetadata | None,
    hint: TokenMetadata | None,
) -> TokenMetadata:
    status = normalized_logo_status(cached.logo_status if cached is not None else None)
    candidates = build_logo_candidates(
        chain_id=chain_id,
        address=address,
        provider_logo_url=hint.logo_url if hint is not None else None,
        cached_logo_url=cached.logo_url if cached is not None else metadata.logo_url,
    )

    chosen_logo_url: str | None
    if status == "valid":
        if cached is not None and cached.logo_url:
            chosen_logo_url = cached.logo_url
        else:
            chosen_logo_url = candidates[0].url if candidates else None
    elif status == "invalid":
        chosen_logo_url = None
    else:
        chosen_logo_url = candidates[0].url if candidates else None

    return metadata.model_copy(
        update={
            "logo_url": chosen_logo_url,
            "logo_status": status,
            "logo_checked_at": cached.logo_checked_at if cached is not None else None,
            "logo_http_status": cached.logo_http_status if cached is not None else None,
        }
    )


def normalized_logo_status(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = value.strip().lower()
    if normalized in {"unknown", "valid", "invalid"}:
        return normalized
    return "unknown"


def _is_native(*, address: str, cached: TokenMetadata | None, hint: TokenMetadata | None) -> bool:
    if cached is not None and cached.is_native:
        return True
    if hint is not None and hint.is_native:
        return True
    return address.lower() == NATIVE_TOKEN_ALIAS.lower()


def _pick_first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        return value
    return None

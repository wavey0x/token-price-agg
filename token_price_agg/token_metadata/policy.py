from __future__ import annotations

import time
from typing import Any

from token_price_agg.core.models import TokenMetadata, TokenRef
from token_price_agg.core.validator import AddressValidator

_VALID_RECHECK_SECONDS = 14 * 86400  # 14 days
_INVALID_RECHECK_SECONDS = 2 * 86400  # 2 days


def hints_from_refs(refs: list[TokenRef], *, chain_id: int) -> dict[str, TokenMetadata]:
    out: dict[str, TokenMetadata] = {}
    for ref in refs:
        hint = TokenMetadata(
            chain_id=chain_id,
            address=ref.address,
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


def collect_provider_logo_urls(
    refs: list[TokenRef], *, chain_id: int
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ref in refs:
        address = AddressValidator.normalize_address(ref.address)
        if ref.logo_url:
            urls = out.setdefault(address, [])
            if ref.logo_url not in urls:
                urls.append(ref.logo_url)
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
    provider_logo_urls: list[str] | None = None,
) -> TokenMetadata:
    status = normalized_logo_status(cached.logo_status if cached is not None else None)
    checked_at = cached.logo_checked_at if cached is not None else None

    if status in ("valid", "invalid") and _is_stale(status, checked_at):
        status = "unknown"

    if status == "valid" and cached is not None and cached.logo_url:
        return metadata.model_copy(
            update={
                "logo_url": cached.logo_url,
                "logo_status": "valid",
                "logo_source": cached.logo_source if cached is not None else None,
                "logo_checked_at": checked_at,
                "logo_http_status": cached.logo_http_status if cached is not None else None,
            }
        )

    if status == "invalid":
        return metadata.model_copy(
            update={
                "logo_url": None,
                "logo_status": "invalid",
                "logo_source": None,
                "logo_checked_at": checked_at,
                "logo_http_status": cached.logo_http_status if cached is not None else None,
            }
        )

    # status == "unknown": return first provider URL ephemerally (not persisted)
    # but do not return unverified static fallbacks
    ephemeral_url: str | None = None
    for url in provider_logo_urls or []:
        ephemeral_url = url
        break

    return metadata.model_copy(
        update={
            "logo_url": ephemeral_url,
            "logo_status": "unknown",
            "logo_source": None,
            "logo_checked_at": checked_at,
            "logo_http_status": None,
        }
    )


def normalized_logo_status(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = value.strip().lower()
    if normalized in {"unknown", "valid", "invalid"}:
        return normalized
    return "unknown"


def _is_stale(status: str, checked_at: int | None) -> bool:
    if checked_at is None:
        return True
    age = int(time.time()) - checked_at
    if status == "valid":
        return age > _VALID_RECHECK_SECONDS
    if status == "invalid":
        return age > _INVALID_RECHECK_SECONDS
    return True


def _is_native(*, address: str, cached: TokenMetadata | None, hint: TokenMetadata | None) -> bool:
    if cached is not None and AddressValidator.is_native_alias(cached.address):
        return True
    if hint is not None and AddressValidator.is_native_alias(hint.address):
        return True
    return AddressValidator.is_native_alias(address)


def _pick_first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        return value
    return None

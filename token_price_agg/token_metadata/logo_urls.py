from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LogoCandidate:
    source: str
    url: str


def build_logo_candidates(
    *,
    chain_id: int,
    address: str,
    provider_logo_urls: list[str] | None = None,
    cached_logo_url: str | None = None,
) -> list[LogoCandidate]:
    candidates: list[LogoCandidate] = []
    seen: set[str] = set()

    def _add(source: str, url: str | None) -> None:
        if url is None:
            return
        normalized = url.strip()
        if not normalized:
            return
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(LogoCandidate(source=source, url=normalized))

    for provider_url in provider_logo_urls or []:
        _add("provider", provider_url)
    _add("cached", cached_logo_url)
    _add("yearn_tokenassets", yearn_tokenassets_logo_url(chain_id=chain_id, address=address))
    _add("trustwallet", trustwallet_logo_url(chain_id=chain_id, address=address))
    _add("smoldapp", smoldapp_logo_url(chain_id=chain_id, address=address))
    return candidates


def smoldapp_logo_url(*, chain_id: int, address: str) -> str:
    return f"https://assets.smold.app/api/token/{chain_id}/{address.lower()}/logo-128.png"


def yearn_tokenassets_logo_url(*, chain_id: int, address: str) -> str:
    return (
        "https://raw.githubusercontent.com/yearn/tokenAssets/main/"
        f"tokens/{chain_id}/{address.lower()}/logo-128.png"
    )


def trustwallet_logo_url(*, chain_id: int, address: str) -> str | None:
    if chain_id != 1:
        return None
    return (
        "https://raw.githubusercontent.com/trustwallet/assets/master/"
        f"blockchains/ethereum/assets/{address}/logo.png"
    )

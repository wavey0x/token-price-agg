from __future__ import annotations

import logging
import time
from typing import ClassVar, Protocol

import httpx

from token_price_agg.core.validator import AddressValidator
from token_price_agg.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache
from token_price_agg.token_metadata.logo_urls import LogoCandidate

_LOGGER = logging.getLogger("token_price_agg.token_metadata")

_SOURCE_REFRESH_SECONDS = 12 * 3600
_HTTP_TIMEOUT_S = 15.0
_HTTP_HEADERS = {"User-Agent": "token-price-agg/logo-source-sync"}


class LocalTokenLogoOverrideSource:
    id = "local_override"
    _CHAIN_LOGO_OVERRIDES: ClassVar[dict[int, dict[str, str]]] = {
        # Checked-in escape hatch for tokens missing from upstream lists.
        1: {
            "0x7fE24F1A024D33506966CB7CA48Bab8c65fB632d": "https://www.asymmetry.finance/ASF-32x32.png",
        }
    }

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id in self._CHAIN_LOGO_OVERRIDES

    async def fetch_entries(self, *, chain_id: int) -> list[TokenLogoSourceEntry]:
        overrides = self._CHAIN_LOGO_OVERRIDES.get(chain_id, {})
        entries: list[TokenLogoSourceEntry] = []
        for address, logo_url in overrides.items():
            entries.append(
                TokenLogoSourceEntry(
                    source=self.id,
                    chain_id=chain_id,
                    address=AddressValidator.normalize_address(address),
                    logo_url=logo_url,
                )
            )
        return entries


class TokenLogoSource(Protocol):
    id: str

    def supports_chain(self, chain_id: int) -> bool: ...

    async def fetch_entries(self, *, chain_id: int) -> list[TokenLogoSourceEntry]: ...


class CoinGeckoTokenListSource:
    id = "coingecko"
    _CHAIN_URLS: ClassVar[dict[int, str]] = {
        1: "https://tokens.coingecko.com/ethereum/all.json",
    }

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id in self._CHAIN_URLS

    async def fetch_entries(self, *, chain_id: int) -> list[TokenLogoSourceEntry]:
        url = self._CHAIN_URLS.get(chain_id)
        if url is None:
            return []

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT_S),
            follow_redirects=True,
            headers=_HTTP_HEADERS,
        ) as client:
            response = await client.get(url)
        response.raise_for_status()

        payload = response.json()
        return self._parse_entries(chain_id=chain_id, payload=payload)

    @classmethod
    def _parse_entries(
        cls,
        *,
        chain_id: int,
        payload: object,
    ) -> list[TokenLogoSourceEntry]:
        if not isinstance(payload, dict):
            raise ValueError("CoinGecko token list payload must be a JSON object")

        raw_tokens = payload.get("tokens")
        if not isinstance(raw_tokens, list):
            raise ValueError("CoinGecko token list payload missing tokens array")

        entries: list[TokenLogoSourceEntry] = []
        seen: set[str] = set()
        for raw_token in raw_tokens:
            if not isinstance(raw_token, dict):
                continue

            token_chain_id = _parse_int(raw_token.get("chainId"))
            if token_chain_id is not None and token_chain_id != chain_id:
                continue

            address = _parse_str(raw_token.get("address"))
            logo_url = _parse_str(raw_token.get("logoURI"))
            if address is None or logo_url is None or not logo_url.startswith("https://"):
                continue

            try:
                normalized = AddressValidator.normalize_address(address)
            except Exception:
                continue

            if normalized in seen:
                continue
            seen.add(normalized)
            entries.append(
                TokenLogoSourceEntry(
                    source=cls.id,
                    chain_id=chain_id,
                    address=normalized,
                    logo_url=logo_url,
                )
            )
        return entries


class TokenLogoSourceManager:
    def __init__(
        self,
        *,
        cache: TokenMetadataCache,
        sources: list[TokenLogoSource] | None = None,
    ) -> None:
        self._cache = cache
        default_sources: list[TokenLogoSource] = [
            LocalTokenLogoOverrideSource(),
            CoinGeckoTokenListSource(),
        ]
        self._sources: list[TokenLogoSource] = list(
            sources if sources is not None else default_sources
        )

    def get_candidates(
        self,
        *,
        chain_id: int,
        addresses: list[str],
    ) -> dict[str, list[LogoCandidate]]:
        rows = self._cache.get_logo_source_entries(chain_id=chain_id, addresses=addresses)
        if not rows:
            return {}

        priority = {source.id: index for index, source in enumerate(self._sources)}
        out: dict[str, list[LogoCandidate]] = {}
        for address, entries in rows.items():
            ordered = sorted(
                entries,
                key=lambda entry: (priority.get(entry.source, len(priority)), entry.source),
            )
            out[address] = [
                LogoCandidate(source=entry.source, url=entry.logo_url) for entry in ordered
            ]
        return out

    def latest_sync_at(self, *, chain_id: int) -> int | None:
        latest: int | None = None
        for source in self._sources:
            if not source.supports_chain(chain_id):
                continue
            state = self._cache.get_logo_source_sync_state(source=source.id, chain_id=chain_id)
            if state is None:
                continue
            if latest is None or state.synced_at > latest:
                latest = state.synced_at
        return latest

    async def refresh_sources(
        self,
        *,
        chain_id: int,
        force: bool = False,
    ) -> dict[str, int]:
        now = int(time.time())
        refreshed: dict[str, int] = {}

        for source in self._sources:
            if not source.supports_chain(chain_id):
                continue

            state = self._cache.get_logo_source_sync_state(source=source.id, chain_id=chain_id)
            if (
                not force
                and state is not None
                and now - state.synced_at < _SOURCE_REFRESH_SECONDS
            ):
                continue

            try:
                entries = await source.fetch_entries(chain_id=chain_id)
            except Exception:
                _LOGGER.exception(
                    "token_logo_source_refresh_failed",
                    extra={"source": source.id, "chain_id": chain_id},
                )
                continue

            self._cache.replace_logo_source_entries(
                source=source.id,
                chain_id=chain_id,
                entries=entries,
            )
            self._cache.upsert_logo_source_sync_state(
                source=source.id,
                chain_id=chain_id,
                synced_at=now,
            )
            refreshed[source.id] = len(entries)
            _LOGGER.info(
                "token_logo_source_refreshed",
                extra={
                    "source": source.id,
                    "chain_id": chain_id,
                    "entry_count": len(entries),
                },
            )

        return refreshed


def _parse_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None

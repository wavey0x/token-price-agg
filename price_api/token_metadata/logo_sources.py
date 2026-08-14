from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import ClassVar, Protocol, cast
from urllib.parse import SplitResult, urlsplit

import httpx

from price_api.core.validator import AddressValidator
from price_api.token_metadata.cache import TokenLogoSourceEntry, TokenMetadataCache

_LOGGER = logging.getLogger("price_api.token_logos")
_SOURCE_HEADERS = {"User-Agent": "price-api/token-logo-source-refresh"}
_MAX_METADATA_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class LogoCandidate:
    source: str
    url: str


class LogoSource(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def metadata_url(self) -> str | None: ...

    def supports_chain(self, chain_id: int) -> bool: ...

    def deterministic_candidate(self, *, chain_id: int, address: str) -> str | None: ...

    def parse_metadata(self, *, chain_id: int, payload: object) -> list[TokenLogoSourceEntry]: ...

    def allows_image_url(self, *, chain_id: int, address: str, url: str) -> bool: ...


class YearnTokenAssetsSource:
    id = "yearn_tokenassets"
    metadata_url = None

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id > 0

    def deterministic_candidate(self, *, chain_id: int, address: str) -> str:
        normalized = AddressValidator.normalize_address(address)
        return (
            "https://raw.githubusercontent.com/yearn/tokenAssets/main/"
            f"tokens/{chain_id}/{normalized.lower()}/logo-128.png"
        )

    def parse_metadata(self, *, chain_id: int, payload: object) -> list[TokenLogoSourceEntry]:
        del chain_id, payload
        return []

    def allows_image_url(self, *, chain_id: int, address: str, url: str) -> bool:
        return _matches_exact_url(
            url,
            self.deterministic_candidate(chain_id=chain_id, address=address),
        )


class TrustWalletSource:
    id = "trustwallet"
    metadata_url = None

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id == 1

    def deterministic_candidate(self, *, chain_id: int, address: str) -> str | None:
        if not self.supports_chain(chain_id):
            return None
        normalized = AddressValidator.normalize_address(address)
        return (
            "https://raw.githubusercontent.com/trustwallet/assets/master/"
            f"blockchains/ethereum/assets/{normalized}/logo.png"
        )

    def parse_metadata(self, *, chain_id: int, payload: object) -> list[TokenLogoSourceEntry]:
        del chain_id, payload
        return []

    def allows_image_url(self, *, chain_id: int, address: str, url: str) -> bool:
        expected = self.deterministic_candidate(chain_id=chain_id, address=address)
        return expected is not None and _matches_exact_url(url, expected)


class CoinGeckoSource:
    id = "coingecko"
    metadata_url = "https://tokens.coingecko.com/ethereum/all.json"
    _IMAGE_PATH = re.compile(r"^/coins/images/[A-Za-z0-9_./%-]+\.(?:png|jpe?g|webp)$", re.I)
    _IMAGE_HOSTS: ClassVar[set[str]] = {
        "assets.coingecko.com",
        "coin-images.coingecko.com",
    }

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id == 1

    def deterministic_candidate(self, *, chain_id: int, address: str) -> None:
        del chain_id, address
        return None

    def parse_metadata(self, *, chain_id: int, payload: object) -> list[TokenLogoSourceEntry]:
        if not isinstance(payload, dict) or not isinstance(payload.get("tokens"), list):
            raise ValueError("CoinGecko token list payload is missing its tokens array")

        entries: list[TokenLogoSourceEntry] = []
        seen: set[str] = set()
        for raw_token in payload["tokens"]:
            if not isinstance(raw_token, dict):
                continue
            raw_chain_id = _parse_int(raw_token.get("chainId"))
            if raw_chain_id is not None and raw_chain_id != chain_id:
                continue
            raw_address = _parse_str(raw_token.get("address"))
            raw_url = _parse_str(raw_token.get("logoURI"))
            if raw_address is None or raw_url is None:
                continue
            try:
                address = AddressValidator.normalize_address(raw_address)
            except Exception:
                continue
            if address in seen or not self.allows_image_url(
                chain_id=chain_id,
                address=address,
                url=raw_url,
            ):
                continue
            seen.add(address)
            entries.append(
                TokenLogoSourceEntry(
                    source=self.id,
                    chain_id=chain_id,
                    address=address,
                    logo_url=raw_url,
                )
            )
        return entries

    def allows_image_url(self, *, chain_id: int, address: str, url: str) -> bool:
        del address
        if not self.supports_chain(chain_id):
            return False
        parsed = _safe_https_url(url)
        return (
            parsed is not None
            and parsed.hostname in self._IMAGE_HOSTS
            and self._IMAGE_PATH.fullmatch(parsed.path) is not None
            and "%" not in parsed.path
            and all(segment not in {".", ".."} for segment in parsed.path.split("/"))
        )


class SmolDappSource:
    id = "smoldapp"
    metadata_url = None

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id > 0

    def deterministic_candidate(self, *, chain_id: int, address: str) -> str:
        normalized = AddressValidator.normalize_address(address)
        return (
            "https://cdn.jsdelivr.net/gh/SmolDapp/tokenAssets@main/"
            f"tokens/{chain_id}/{normalized.lower()}/logo-128.png"
        )

    def parse_metadata(self, *, chain_id: int, payload: object) -> list[TokenLogoSourceEntry]:
        del chain_id, payload
        return []

    def allows_image_url(self, *, chain_id: int, address: str, url: str) -> bool:
        return _matches_exact_url(
            url,
            self.deterministic_candidate(chain_id=chain_id, address=address),
        )


# This tuple is the complete reviewed remote-acquisition boundary and its priority order.
LOGO_SOURCES: tuple[LogoSource, ...] = (
    cast(LogoSource, YearnTokenAssetsSource()),
    cast(LogoSource, TrustWalletSource()),
    cast(LogoSource, CoinGeckoSource()),
    cast(LogoSource, SmolDappSource()),
)


class TokenLogoSourceManager:
    def __init__(
        self,
        *,
        cache: TokenMetadataCache,
        sources: tuple[LogoSource, ...] = LOGO_SOURCES,
    ) -> None:
        self._cache = cache
        self._sources = sources
        self._by_id = {source.id: source for source in sources}
        if len(self._by_id) != len(sources):
            raise ValueError("logo source IDs must be unique")

    @property
    def sources(self) -> tuple[LogoSource, ...]:
        return self._sources

    def source_for_id(self, source_id: str) -> LogoSource | None:
        return self._by_id.get(source_id)

    def get_candidates(self, *, chain_id: int, address: str) -> list[LogoCandidate]:
        normalized = AddressValidator.normalize_address(address)
        stored = self._cache.get_logo_source_entries(
            chain_id=chain_id,
            addresses=[normalized],
        ).get(normalized, [])
        stored_by_source = {entry.source: entry.logo_url for entry in stored}

        candidates: list[LogoCandidate] = []
        seen: set[str] = set()
        for source in self._sources:
            if not source.supports_chain(chain_id):
                continue
            urls = [
                source.deterministic_candidate(chain_id=chain_id, address=normalized),
                stored_by_source.get(source.id),
            ]
            for url in urls:
                if url is None or url in seen:
                    continue
                if not source.allows_image_url(
                    chain_id=chain_id,
                    address=normalized,
                    url=url,
                ):
                    continue
                seen.add(url)
                candidates.append(LogoCandidate(source=source.id, url=url))
        return candidates

    async def refresh_sources(
        self,
        *,
        chain_ids: list[int],
        refresh_interval_ms: int,
        force: bool = False,
    ) -> dict[int, dict[str, int]]:
        now = time.time_ns() // 1_000_000
        refreshed: dict[int, dict[str, int]] = {}
        timeout = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers=_SOURCE_HEADERS,
        ) as client:
            for chain_id in chain_ids:
                chain_result: dict[str, int] = {}
                for source in self._sources:
                    if source.metadata_url is None or not source.supports_chain(chain_id):
                        continue
                    state = await asyncio.to_thread(
                        self._cache.get_logo_source_sync_state,
                        source=source.id,
                        chain_id=chain_id,
                    )
                    if (
                        not force
                        and state is not None
                        and now - state.synced_at < refresh_interval_ms
                    ):
                        continue
                    try:
                        payload = await _fetch_metadata(client=client, source=source)
                        entries = source.parse_metadata(chain_id=chain_id, payload=payload)
                        await asyncio.to_thread(
                            self._cache.replace_logo_source_entries,
                            source=source.id,
                            chain_id=chain_id,
                            entries=entries,
                            synced_at=now,
                        )
                    except Exception as exc:
                        _LOGGER.warning(
                            "token_logo_source_refresh_failed",
                            extra={
                                "source": source.id,
                                "chain_id": chain_id,
                                "error_code": type(exc).__name__[:64],
                            },
                        )
                        continue
                    chain_result[source.id] = len(entries)
                    _LOGGER.info(
                        "token_logo_source_refreshed",
                        extra={
                            "source": source.id,
                            "chain_id": chain_id,
                            "entry_count": len(entries),
                        },
                    )
                if chain_result:
                    refreshed[chain_id] = chain_result
        return refreshed


async def _fetch_metadata(*, client: httpx.AsyncClient, source: LogoSource) -> object:
    url = source.metadata_url
    if url is None or _safe_https_url(url) is None:
        raise ValueError("invalid fixed logo metadata endpoint")
    async with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise ValueError(f"metadata_http_{response.status_code}")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                parsed_content_length = int(content_length)
            except ValueError:
                parsed_content_length = None
            if (
                parsed_content_length is not None
                and parsed_content_length > _MAX_METADATA_BYTES
            ):
                raise ValueError("metadata_too_large")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            body.extend(chunk)
            if len(body) > _MAX_METADATA_BYTES:
                raise ValueError("metadata_too_large")
    return json.loads(body)


def _matches_exact_url(actual: str, expected: str) -> bool:
    parsed = _safe_https_url(actual)
    return parsed is not None and actual == expected and not parsed.query


def _safe_https_url(url: str) -> SplitResult | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        return None
    return parsed


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

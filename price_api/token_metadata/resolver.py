from __future__ import annotations

import asyncio
import logging

from price_api.app.config import Settings
from price_api.core.models import PriceResult, QuoteResult, TokenMetadata, TokenRef
from price_api.core.validator import AddressValidator
from price_api.token_metadata.cache import TokenMetadataCache
from price_api.token_metadata.logo_service import TokenLogoService
from price_api.token_metadata.logo_sources import TokenLogoSourceManager
from price_api.token_metadata.onchain import fetch_onchain_metadata
from price_api.token_metadata.policy import hints_from_refs, merge_metadata
from price_api.web3.client import AsyncRpcClient

_LOGGER = logging.getLogger("price_api.token_metadata")


class TokenMetadataResolver:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache = TokenMetadataCache(db_path=settings.token_metadata_db_path)
        self._rpc = AsyncRpcClient(
            rpc_urls=settings.rpc_urls,
            request_timeout_s=settings.rpc_request_timeout_ms / 1000,
        )
        self._logo_sources = TokenLogoSourceManager(cache=self._cache)
        self._logo_service = TokenLogoService(
            cache=self._cache,
            source_manager=self._logo_sources,
        )

    @property
    def cache(self) -> TokenMetadataCache:
        return self._cache

    async def start(self) -> None:
        await self._logo_service.start(chain_ids=self._settings.chain_ids)

    async def aclose(self) -> None:
        await self._logo_service.aclose()
        await self._rpc.aclose()

    async def resolve_from_price_results(
        self,
        *,
        chain_id: int,
        request_token: TokenRef,
        results: list[PriceResult],
    ) -> dict[str, TokenMetadata]:
        refs = [request_token]
        for result in results:
            if result.token is not None:
                refs.append(result.token)
            if result.vault_context is not None:
                underlying = result.vault_context.underlying_token
                if underlying is not None:
                    refs.append(TokenRef(chain_id=chain_id, address=underlying))
        return await self._resolve(chain_id=chain_id, refs=refs, source="provider")

    async def resolve_from_quote_results(
        self,
        *,
        chain_id: int,
        request_token_in: TokenRef,
        request_token_out: TokenRef,
        results: list[QuoteResult],
    ) -> dict[str, TokenMetadata]:
        refs = [request_token_in, request_token_out]
        for result in results:
            if result.token_in is not None:
                refs.append(result.token_in)
            if result.token_out is not None:
                refs.append(result.token_out)
            if result.vault_context is not None:
                for underlying in (
                    result.vault_context.underlying_token,
                    result.vault_context.underlying_token_in,
                    result.vault_context.underlying_token_out,
                ):
                    if underlying is not None:
                        refs.append(TokenRef(chain_id=chain_id, address=underlying))
        return await self._resolve(chain_id=chain_id, refs=refs, source="provider")

    async def resolve_token(
        self,
        *,
        chain_id: int,
        request_token: TokenRef,
    ) -> dict[str, TokenMetadata]:
        return await self._resolve(
            chain_id=chain_id,
            refs=[request_token],
            source="token_request",
        )

    async def observe_identities(self, *, chain_id: int, addresses: list[str]) -> None:
        await asyncio.to_thread(
            self._cache.enroll_observed,
            chain_id=chain_id,
            addresses=addresses,
        )

    async def _resolve(
        self,
        *,
        chain_id: int,
        refs: list[TokenRef],
        source: str,
    ) -> dict[str, TokenMetadata]:
        unique_addresses = list(
            dict.fromkeys(AddressValidator.normalize_address(ref.address) for ref in refs)
        )
        if not unique_addresses:
            return {}

        cached = await asyncio.to_thread(
            self._cache.get_many,
            chain_id=chain_id,
            addresses=unique_addresses,
        )
        hinted = hints_from_refs(refs, chain_id=chain_id)

        merged: dict[str, TokenMetadata] = {}
        for address in unique_addresses:
            merged[address] = merge_metadata(
                chain_id=chain_id,
                address=address,
                cached=cached.get(address),
                hint=hinted.get(address),
                default_source=source,
            )

        unresolved = [
            address
            for address, metadata in merged.items()
            if not AddressValidator.is_native_alias(metadata.address)
            and (metadata.symbol is None or metadata.decimals is None)
        ]
        onchain = await self._fetch_onchain_metadata(chain_id=chain_id, addresses=unresolved)
        for address, value in onchain.items():
            merged[address] = merge_metadata(
                chain_id=chain_id,
                address=address,
                cached=merged.get(address),
                hint=value,
                default_source="onchain_multicall",
            )

        to_persist: list[TokenMetadata] = []
        for metadata in merged.values():
            stored = metadata.model_copy(update={"logo_url": None})
            if stored != cached.get(stored.address):
                to_persist.append(stored)
        if to_persist:
            await asyncio.to_thread(self._cache.upsert_many, to_persist)

        await self.observe_identities(chain_id=chain_id, addresses=unique_addresses)
        return merged

    async def _fetch_onchain_metadata(
        self,
        *,
        chain_id: int,
        addresses: list[str],
    ) -> dict[str, TokenMetadata]:
        try:
            return await fetch_onchain_metadata(
                chain_id=chain_id,
                addresses=addresses,
                rpc_client=self._rpc,
            )
        except Exception:
            _LOGGER.exception("token_multicall_failed", extra={"chain_id": chain_id})
            return {}

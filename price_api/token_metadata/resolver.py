from __future__ import annotations

import asyncio
import logging

from price_api.app.config import Settings
from price_api.core.models import PriceResult, QuoteResult, TokenMetadata, TokenRef
from price_api.core.validator import AddressValidator
from price_api.token_metadata.cache import TokenMetadataCache
from price_api.token_metadata.logo_sources import TokenLogoSourceManager
from price_api.token_metadata.logo_urls import LogoCandidate, build_logo_candidates
from price_api.token_metadata.logo_verifier import apply_verify_result, verify_candidates
from price_api.token_metadata.onchain import fetch_onchain_metadata
from price_api.token_metadata.policy import (
    collect_provider_logo_urls,
    hints_from_refs,
    merge_metadata,
    resolve_logo_for_response,
)
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
        self._pending_verification: set[tuple[int, str]] = set()
        self._verification_tasks: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        tasks = list(self._verification_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._verification_tasks.clear()
        self._pending_verification.clear()
        await self._rpc.aclose()

    async def refresh_logo_sources(self, *, force: bool = False) -> dict[int, dict[str, int]]:
        refreshed: dict[int, dict[str, int]] = {}
        for chain_id in self._settings.chain_ids:
            refreshed[chain_id] = await self._logo_sources.refresh_sources(
                chain_id=chain_id,
                force=force,
            )
        return refreshed

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
                underlying = result.vault_context.underlying_token
                if underlying is not None:
                    refs.append(TokenRef(chain_id=chain_id, address=underlying))
                underlying_in = result.vault_context.underlying_token_in
                if underlying_in is not None:
                    refs.append(TokenRef(chain_id=chain_id, address=underlying_in))
                underlying_out = result.vault_context.underlying_token_out
                if underlying_out is not None:
                    refs.append(TokenRef(chain_id=chain_id, address=underlying_out))
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

    async def _resolve(
        self,
        *,
        chain_id: int,
        refs: list[TokenRef],
        source: str,
    ) -> dict[str, TokenMetadata]:
        unique_addresses: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            if ref.address in seen:
                continue
            seen.add(ref.address)
            unique_addresses.append(ref.address)

        if not unique_addresses:
            return {}

        cached = await asyncio.to_thread(
            self._cache.get_many,
            chain_id=chain_id,
            addresses=unique_addresses,
        )
        hinted = hints_from_refs(refs, chain_id=chain_id)
        provider_logos = collect_provider_logo_urls(refs, chain_id=chain_id)
        candidates_task = asyncio.to_thread(
            self._logo_sources.get_candidates,
            chain_id=chain_id,
            addresses=unique_addresses,
        )
        sync_task = asyncio.to_thread(self._logo_sources.latest_sync_at, chain_id=chain_id)
        source_logo_candidates, latest_source_sync_at = await asyncio.gather(
            candidates_task,
            sync_task,
        )

        merged: dict[str, TokenMetadata] = {}
        for address in unique_addresses:
            metadata = cached.get(address)
            hint = hinted.get(address)
            merged[address] = merge_metadata(
                chain_id=chain_id,
                address=address,
                cached=metadata,
                hint=hint,
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

        needs_verification: list[str] = []
        for address, metadata in list(merged.items()):
            merged[address] = resolve_logo_for_response(
                chain_id=chain_id,
                address=address,
                metadata=metadata,
                cached=cached.get(address),
                provider_logo_urls=provider_logos.get(address),
                latest_source_sync_at=latest_source_sync_at,
            )
            if merged[address].logo_status == "unknown":
                needs_verification.append(address)

        # Persist metadata, but don't store unverified logo URLs
        to_persist = []
        for metadata in merged.values():
            if metadata.logo_status == "unknown":
                to_persist.append(metadata.model_copy(update={"logo_url": None}))
            else:
                to_persist.append(metadata)
        await asyncio.to_thread(self._cache.upsert_many, to_persist)

        # Background-verify tokens with unknown logo status
        for address in needs_verification:
            self._enqueue_verification(
                chain_id=chain_id,
                address=address,
                provider_logo_urls=provider_logos.get(address),
                source_logo_candidates=source_logo_candidates.get(address),
                existing=cached.get(address),
            )

        return merged

    def _enqueue_verification(
        self,
        *,
        chain_id: int,
        address: str,
        provider_logo_urls: list[str] | None,
        source_logo_candidates: list[LogoCandidate] | None,
        existing: TokenMetadata | None,
    ) -> None:
        key = (chain_id, address)
        if key in self._pending_verification:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if len(self._verification_tasks) >= self._settings.token_logo_max_pending_verifications:
            _LOGGER.debug(
                "background_logo_verify_capacity_reached",
                extra={"chain_id": chain_id, "address": address},
            )
            return

        self._pending_verification.add(key)

        task = loop.create_task(
            self._verify_and_persist(
                chain_id=chain_id,
                address=address,
                provider_logo_urls=provider_logo_urls,
                source_logo_candidates=source_logo_candidates,
                existing=existing,
            )
        )
        self._verification_tasks.add(task)
        task.add_done_callback(self._verification_tasks.discard)

    async def _verify_and_persist(
        self,
        *,
        chain_id: int,
        address: str,
        provider_logo_urls: list[str] | None,
        source_logo_candidates: list[LogoCandidate] | None,
        existing: TokenMetadata | None,
    ) -> None:
        try:
            candidates = build_logo_candidates(
                chain_id=chain_id,
                address=address,
                provider_logo_urls=provider_logo_urls,
                cached_logo_url=existing.logo_url if existing is not None else None,
                additional_logo_candidates=source_logo_candidates,
            )
            result = await verify_candidates(candidates)

            base = existing or TokenMetadata(chain_id=chain_id, address=address)
            updated = apply_verify_result(base, result)
            await asyncio.to_thread(self._cache.upsert_logo_verification, updated)

            _LOGGER.debug(
                "background_logo_verified",
                extra={
                    "chain_id": chain_id,
                    "address": address,
                    "logo_status": result.logo_status,
                    "logo_url": result.logo_url,
                    "logo_source": result.logo_source,
                },
            )
        except Exception:
            _LOGGER.exception(
                "background_logo_verify_failed",
                extra={"chain_id": chain_id, "address": address},
            )
        finally:
            self._pending_verification.discard((chain_id, address))

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

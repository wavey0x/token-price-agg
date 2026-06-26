from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from price_api.app.config import Settings
from price_api.core.errors import InvalidRequestError
from price_api.core.models import (
    ProviderPriceRequest,
    ProviderQuoteRequest,
    TokenRef,
    VaultContext,
    VaultType,
)
from price_api.observability.metrics import record_vault_resolution
from price_api.vault.adapters.erc4626 import Erc4626Adapter, Erc4626VaultInfo
from price_api.vault.adapters.yearn_v2 import YearnV2Adapter, YearnV2VaultInfo
from price_api.web3.client import AsyncRpcClient


class VaultResolver:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rpc_client = AsyncRpcClient(rpc_urls=settings.rpc_urls)
        self._erc4626 = Erc4626Adapter(self._rpc_client)
        self._yearn_v2 = YearnV2Adapter(self._rpc_client)
        self._semaphore = asyncio.Semaphore(settings.web3_limit)
        self._positive_cache: dict[tuple[int, str], _VaultCacheEntry] = {}
        self._negative_cache: dict[tuple[int, str], float] = {}

    async def resolve_price_request(
        self,
        req: ProviderPriceRequest,
    ) -> tuple[ProviderPriceRequest, VaultContext]:
        started = time.perf_counter()
        if not self._rpc_client.configured():
            record_vault_resolution(
                result="rpc_not_configured",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            raise InvalidRequestError("RPC_NOT_CONFIGURED", "Vault resolution requires RPC_URLS")

        vault = await self._detect_vault(req.token.address, req.chain_id)
        if vault is None:
            record_vault_resolution(
                result="not_vault",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            raise InvalidRequestError("INVALID_VAULT", "Token is not a supported vault")

        underlying = _underlying_token_ref(req.token, vault.underlying_token)
        converted = ProviderPriceRequest(chain_id=req.chain_id, token=underlying)
        context = _vault_context(vault, await self._rpc_client.block_number())
        record_vault_resolution(
            result="success",
            vault_type=vault.vault_type.value,
            duration_seconds=time.perf_counter() - started,
        )
        return converted, context

    async def resolve_quote_request(
        self,
        req: ProviderQuoteRequest,
    ) -> tuple[ProviderQuoteRequest, QuoteVaultResolution | None]:
        started = time.perf_counter()
        if not self._rpc_client.configured():
            record_vault_resolution(
                result="rpc_not_configured",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            raise InvalidRequestError("RPC_NOT_CONFIGURED", "Vault resolution requires RPC_URLS")

        token_in = req.token_in
        token_out = req.token_out
        amount_in = req.amount_in

        vault_in = await self._detect_vault(token_in.address, req.chain_id)
        if vault_in is not None:
            token_in = _underlying_token_ref(token_in, vault_in.underlying_token)
            amount_in = vault_in.convert_shares_to_assets(amount_in)

        vault_out = await self._detect_vault(token_out.address, req.chain_id)
        if vault_out is not None:
            token_out = _underlying_token_ref(token_out, vault_out.underlying_token)

        if vault_in is None and vault_out is None:
            record_vault_resolution(
                result="not_vault",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            raise InvalidRequestError(
                "INVALID_VAULT",
                "use_underlying=true provided but neither token_in nor token_out "
                "is a supported vault",
            )

        converted = ProviderQuoteRequest(
            chain_id=req.chain_id,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
        )
        block_number = await self._rpc_client.block_number()
        resolution = QuoteVaultResolution(
            input_vault_context=(
                _vault_context(vault_in, block_number) if vault_in is not None else None
            ),
            output_vault_context=(
                _vault_context(vault_out, block_number) if vault_out is not None else None
            ),
            output_assets_to_shares=(
                vault_out.convert_assets_to_shares if vault_out is not None else None
            ),
        )
        vault_type = _resolved_vault_type(vault_in=vault_in, vault_out=vault_out)
        record_vault_resolution(
            result="success",
            vault_type=vault_type,
            duration_seconds=time.perf_counter() - started,
        )
        return converted, resolution

    async def _detect_vault(self, address: str, chain_id: int) -> _VaultInfo | None:
        key = (chain_id, address.lower())
        now = time.monotonic()
        cache_hit, cached = self._cached_vault(key=key, now=now)
        if cache_hit:
            return cached

        async with self._semaphore:
            now = time.monotonic()
            cache_hit, cached = self._cached_vault(key=key, now=now)
            if cache_hit:
                return cached

            erc4626 = await self._erc4626.detect(address, chain_id)
            if erc4626 is not None:
                vault = _VaultInfo.from_erc4626(erc4626)
                self._positive_cache[key] = _VaultCacheEntry(
                    vault=vault,
                    expires_at=time.monotonic() + self._settings.vault_positive_cache_ttl_s,
                )
                self._negative_cache.pop(key, None)
                return vault

            yearn = await self._yearn_v2.detect(address, chain_id)
            if yearn is not None:
                vault = _VaultInfo.from_yearn_v2(yearn)
                self._positive_cache[key] = _VaultCacheEntry(
                    vault=vault,
                    expires_at=time.monotonic() + self._settings.vault_positive_cache_ttl_s,
                )
                self._negative_cache.pop(key, None)
                return vault

        self._negative_cache[key] = time.monotonic() + self._settings.vault_negative_cache_ttl_s
        return None

    def _cached_vault(
        self,
        *,
        key: tuple[int, str],
        now: float,
    ) -> tuple[bool, _VaultInfo | None]:
        cached = self._positive_cache.get(key)
        if cached is not None and now < cached.expires_at:
            return True, cached.vault

        negative_expires_at = self._negative_cache.get(key)
        if negative_expires_at is not None and now < negative_expires_at:
            return True, None

        return False, None


@dataclass(frozen=True)
class QuoteVaultResolution:
    input_vault_context: VaultContext | None = None
    output_vault_context: VaultContext | None = None
    output_assets_to_shares: Callable[[int], int] | None = None


@dataclass(frozen=True)
class _VaultCacheEntry:
    vault: _VaultInfo
    expires_at: float


class _VaultInfo:
    def __init__(
        self,
        *,
        vault_type: VaultType,
        underlying_token: str,
        assets_per_share_unit: int,
        share_unit: int,
        underlying_unit: int,
        convert_fn: Callable[[int], int],
    ) -> None:
        self.vault_type = vault_type
        self.underlying_token = underlying_token
        self._assets_per_share_unit = assets_per_share_unit
        self._share_unit = share_unit
        self._underlying_unit = underlying_unit
        self._convert_fn = convert_fn

    @classmethod
    def from_erc4626(cls, vault: Erc4626VaultInfo) -> _VaultInfo:
        return cls(
            vault_type=VaultType.ERC4626,
            underlying_token=vault.underlying_token,
            assets_per_share_unit=vault.assets_per_share_unit,
            share_unit=10**vault.share_decimals,
            underlying_unit=10**vault.underlying_decimals,
            convert_fn=vault.convert_shares_to_assets,
        )

    @classmethod
    def from_yearn_v2(cls, vault: YearnV2VaultInfo) -> _VaultInfo:
        return cls(
            vault_type=VaultType.YEARN_V2,
            underlying_token=vault.underlying_token,
            assets_per_share_unit=vault.price_per_share,
            share_unit=10**vault.share_decimals,
            underlying_unit=10**vault.underlying_decimals,
            convert_fn=vault.convert_shares_to_assets,
        )

    def convert_shares_to_assets(self, shares: int) -> int:
        return int(self._convert_fn(shares))

    def convert_assets_to_shares(self, assets: int) -> int:
        if self._assets_per_share_unit == 0:
            raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault share_to_asset_rate")
        return int((assets * self._share_unit) // self._assets_per_share_unit)

    @property
    def price_per_share(self) -> Decimal:
        if self._underlying_unit == 0:
            raise InvalidRequestError("INVALID_VAULT_RATE", "Invalid vault price_per_share")
        return Decimal(self._assets_per_share_unit) / Decimal(self._underlying_unit)


def _underlying_token_ref(base: TokenRef, underlying_address: str) -> TokenRef:
    return TokenRef(
        chain_id=base.chain_id,
        address=underlying_address,
        symbol=base.symbol,
        decimals=base.decimals,
    )


def _vault_context(vault: _VaultInfo, block_number: int) -> VaultContext:
    return VaultContext(
        vault_type=vault.vault_type,
        underlying_token=vault.underlying_token,
        price_per_share=vault.price_per_share,
        block_number=block_number,
    )


def _resolved_vault_type(*, vault_in: _VaultInfo | None, vault_out: _VaultInfo | None) -> str:
    if vault_in is None and vault_out is None:
        return "unknown"
    if vault_in is not None and vault_out is not None:
        if vault_in.vault_type == vault_out.vault_type:
            return vault_in.vault_type.value
        return "mixed"
    if vault_in is not None:
        return vault_in.vault_type.value
    assert vault_out is not None
    return vault_out.vault_type.value

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from token_price_agg.app.config import Settings
from token_price_agg.core.errors import InvalidRequestError
from token_price_agg.core.models import (
    ProviderPriceRequest,
    ProviderQuoteRequest,
    TokenRef,
    VaultContext,
    VaultType,
)
from token_price_agg.observability.metrics import record_vault_resolution
from token_price_agg.vault.adapters.erc4626 import Erc4626Adapter, Erc4626VaultInfo
from token_price_agg.vault.adapters.yearn_v2 import YearnV2Adapter, YearnV2VaultInfo
from token_price_agg.web3.client import AsyncRpcClient


class VaultResolver:
    def __init__(self, settings: Settings) -> None:
        self._rpc_client = AsyncRpcClient(rpc_urls=settings.rpc_urls)
        self._erc4626 = Erc4626Adapter(self._rpc_client)
        self._yearn_v2 = YearnV2Adapter(self._rpc_client)
        self._semaphore = asyncio.Semaphore(settings.web3_limit)

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

        vault = await self._detect_vault(req.token.address)
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
    ) -> tuple[ProviderQuoteRequest, VaultContext | None]:
        started = time.perf_counter()
        if not self._rpc_client.configured():
            record_vault_resolution(
                result="rpc_not_configured",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            raise InvalidRequestError("RPC_NOT_CONFIGURED", "Vault resolution requires RPC_URLS")

        context: VaultContext | None = None
        token_in = req.token_in
        token_out = req.token_out
        amount_in = req.amount_in

        vault_in = await self._detect_vault(token_in.address)
        if vault_in is not None:
            token_in = _underlying_token_ref(token_in, vault_in.underlying_token)
            amount_in = vault_in.convert_shares_to_assets(amount_in)
            context = _vault_context(vault_in, await self._rpc_client.block_number())

        vault_out = await self._detect_vault(token_out.address)
        if vault_out is not None:
            token_out = _underlying_token_ref(token_out, vault_out.underlying_token)
            if context is None:
                context = _vault_context(vault_out, await self._rpc_client.block_number())

        if vault_in is None and vault_out is None:
            record_vault_resolution(
                result="not_vault",
                vault_type="unknown",
                duration_seconds=time.perf_counter() - started,
            )
            raise InvalidRequestError(
                "INVALID_VAULT",
                "is_vault=true provided but neither token_in nor token_out is a supported vault",
            )

        converted = ProviderQuoteRequest(
            chain_id=req.chain_id,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
        )
        vault_type = _resolved_vault_type(vault_in=vault_in, vault_out=vault_out)
        record_vault_resolution(
            result="success",
            vault_type=vault_type,
            duration_seconds=time.perf_counter() - started,
        )
        return converted, context

    async def _detect_vault(self, address: str) -> _VaultInfo | None:
        async with self._semaphore:
            erc4626 = await self._erc4626.detect(address)
            if erc4626 is not None:
                return _VaultInfo.from_erc4626(erc4626)

            yearn = await self._yearn_v2.detect(address)
            if yearn is not None:
                return _VaultInfo.from_yearn_v2(yearn)

        return None


class _VaultInfo:
    def __init__(
        self,
        *,
        vault_type: VaultType,
        underlying_token: str,
        share_to_asset_rate: str,
        convert_fn: Callable[[int], int],
    ) -> None:
        self.vault_type = vault_type
        self.underlying_token = underlying_token
        self._share_to_asset_rate = share_to_asset_rate
        self._convert_fn = convert_fn

    @classmethod
    def from_erc4626(cls, vault: Erc4626VaultInfo) -> _VaultInfo:
        return cls(
            vault_type=VaultType.ERC4626,
            underlying_token=vault.underlying_token,
            share_to_asset_rate=vault.share_to_asset_rate_str(),
            convert_fn=vault.convert_shares_to_assets,
        )

    @classmethod
    def from_yearn_v2(cls, vault: YearnV2VaultInfo) -> _VaultInfo:
        return cls(
            vault_type=VaultType.YEARN_V2,
            underlying_token=vault.underlying_token,
            share_to_asset_rate=vault.share_to_asset_rate_str(),
            convert_fn=vault.convert_shares_to_assets,
        )

    def convert_shares_to_assets(self, shares: int) -> int:
        return int(self._convert_fn(shares))

    @property
    def share_to_asset_rate(self) -> str:
        return self._share_to_asset_rate


def _underlying_token_ref(base: TokenRef, underlying_address: str) -> TokenRef:
    return TokenRef(
        chain_id=base.chain_id,
        address=underlying_address,
        is_native=False,
        symbol=base.symbol,
        decimals=base.decimals,
    )


def _vault_context(vault: _VaultInfo, block_number: int) -> VaultContext:
    return VaultContext(
        vault_type=vault.vault_type,
        underlying_token=vault.underlying_token,
        share_to_asset_rate=vault.share_to_asset_rate,
        block_number=block_number,
    )


def _resolved_vault_type(
    *, vault_in: _VaultInfo | None, vault_out: _VaultInfo | None
) -> str:
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

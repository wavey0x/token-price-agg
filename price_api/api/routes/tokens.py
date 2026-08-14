from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from price_api.api.routes.aggregate_utils import (
    get_request_id,
    metadata_for_address,
    raise_bad_request,
)
from price_api.api.schemas.requests import TokenRequest
from price_api.api.schemas.responses import TokenResponse
from price_api.app.dependencies import get_token_metadata_resolver
from price_api.core.errors import InvalidRequestError
from price_api.core.normalizer import normalize_token_request
from price_api.token_metadata.resolver import TokenMetadataResolver

router = APIRouter(tags=["token"])


@router.get(
    "/v1/token",
    response_model=TokenResponse,
    summary="Get known token metadata",
    description=(
        "Returns cached and locally-known token metadata without calling downstream price or quote "
        "providers. `logo_url` is a deterministic first-party resource identifier; the image route "
        "returns 404 while bytes are unavailable."
    ),
)
async def token(
    request: Request,
    token: Annotated[str, Query(min_length=42)],
    chain_id: Annotated[int, Query(gt=0)] = 1,
    token_metadata_resolver: TokenMetadataResolver = Depends(get_token_metadata_resolver),
) -> TokenResponse:
    payload = TokenRequest(chain_id=chain_id, token=token)
    return await _handle_token_request(
        request=request,
        payload=payload,
        token_metadata_resolver=token_metadata_resolver,
    )


async def _handle_token_request(
    *,
    request: Request,
    payload: TokenRequest,
    token_metadata_resolver: TokenMetadataResolver,
) -> TokenResponse:
    try:
        normalized, original_token = normalize_token_request(
            chain_id=payload.chain_id,
            token=payload.token,
        )
    except InvalidRequestError as exc:
        raise_bad_request(exc)

    response_token = original_token or normalized
    token_metadata = await token_metadata_resolver.resolve_token(
        chain_id=payload.chain_id,
        request_token=normalized,
    )

    if original_token is not None:
        canonical_meta = token_metadata.get(normalized.address)
        if canonical_meta is not None:
            token_metadata[original_token.address] = canonical_meta.model_copy(
                update={"address": original_token.address}
            )
        await token_metadata_resolver.observe_identities(
            chain_id=payload.chain_id,
            addresses=[original_token.address],
        )

    return TokenResponse(
        request_id=get_request_id(request),
        chain_id=payload.chain_id,
        token=metadata_for_address(metadata=token_metadata, token=response_token),
    )

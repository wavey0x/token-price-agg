from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from price_api.app.dependencies import get_token_metadata_resolver
from price_api.core.errors import InvalidRequestError
from price_api.core.validator import AddressValidator
from price_api.observability.metrics import record_logo_public_read
from price_api.token_metadata.resolver import TokenMetadataResolver

router = APIRouter(tags=["token logos"])
_POSITIVE_CHAIN_ID = re.compile(r"^[1-9][0-9]*$")
_MAX_EVM_CHAIN_ID = (1 << 256) - 1
_MAX_SQLITE_INTEGER = (1 << 63) - 1


@router.get(
    "/token-logos/{chain_id}/{address}",
    summary="Get an owned token logo",
    description="Returns a validated first-party raster image without upstream network work.",
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}}},
        304: {"description": "The current image matches If-None-Match."},
        400: {"description": "Malformed chain ID or token address."},
        404: {"description": "No owned image is currently available."},
    },
    openapi_extra={"security": []},
)
async def token_logo(
    chain_id: str,
    address: str,
    request: Request,
    resolver: TokenMetadataResolver = Depends(get_token_metadata_resolver),
) -> Response:
    if len(chain_id) > 78 or _POSITIVE_CHAIN_ID.fullmatch(chain_id) is None:
        return _invalid_identity("chain_id must be a positive integer")
    parsed_chain_id = int(chain_id)
    if parsed_chain_id > _MAX_EVM_CHAIN_ID:
        return _invalid_identity("chain_id is out of range")
    try:
        normalized = AddressValidator.normalize_address(address)
    except InvalidRequestError:
        return _invalid_identity("address must be a valid EVM token address")

    asset = None
    if parsed_chain_id <= _MAX_SQLITE_INTEGER:
        asset = await asyncio.to_thread(
            resolver.cache.get_logo_asset,
            chain_id=parsed_chain_id,
            address=normalized,
        )
    if asset is None:
        record_logo_public_read(result="miss")
        return Response(status_code=404, headers=_asset_headers(max_age=300))

    etag = f'"{asset.content_hash}"'
    headers = _asset_headers(max_age=86400)
    headers["ETag"] = etag
    if _if_none_match_matches(request.headers.get("If-None-Match"), asset.content_hash):
        record_logo_public_read(result="hit")
        return Response(status_code=304, headers=headers)

    record_logo_public_read(result="hit")
    return Response(
        content=asset.image_bytes,
        status_code=200,
        media_type=asset.mime_type,
        headers=headers,
    )


def _invalid_identity(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": {"type": "INVALID_TOKEN_LOGO_IDENTITY", "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _asset_headers(*, max_age: int) -> dict[str, str]:
    return {
        "Cache-Control": f"public, max-age={max_age}",
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Access-Control-Allow-Origin": "*",
    }


def _if_none_match_matches(value: str | None, content_hash: str) -> bool:
    if value is None:
        return False
    index = 0
    while index < len(value):
        index = _skip_characters(value, index, " \t,")
        if index >= len(value):
            return False
        member, index = _parse_etag_member(value, index)
        if member in {"*", content_hash}:
            return True
    return False


def _parse_etag_member(value: str, index: int) -> tuple[str | None, int]:
    if value[index] == "*":
        end = _skip_characters(value, index + 1, " \t")
        if end == len(value) or value[end] == ",":
            return "*", end
        return None, _skip_to_comma(value, end)

    if value.startswith("W/", index):
        index += 2
    if index >= len(value) or value[index] != '"':
        return None, _skip_to_comma(value, index)
    end_quote = value.find('"', index + 1)
    if end_quote == -1:
        return None, len(value)
    member = value[index + 1 : end_quote]
    end = _skip_characters(value, end_quote + 1, " \t")
    if end == len(value) or value[end] == ",":
        return member, end
    return None, _skip_to_comma(value, end)


def _skip_characters(value: str, index: int, characters: str) -> int:
    while index < len(value) and value[index] in characters:
        index += 1
    return index


def _skip_to_comma(value: str, index: int) -> int:
    while index < len(value) and value[index] != ",":
        index += 1
    return index

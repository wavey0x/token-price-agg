from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from price_api.app.dependencies import get_token_metadata_resolver
from price_api.app.main import app
from price_api.core.models import TokenMetadata
from price_api.core.validator import AddressValidator
from price_api.tests.e2e.helpers import token_lower
from price_api.token_metadata.logo_acquirer import validate_logo_bytes
from price_api.token_metadata.logo_overrides import get_logo_override
from price_api.token_metadata.logo_urls import token_logo_url

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WFRAX = "0x853d955aCEf822Db058eb8505911ED77F175b99e"
REMAPPED_REQUEST = "0xa3cc91589feedbbee0cfdc7404041e19cb00f110"
REMAPPED_CANONICAL = "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B"


def test_token_endpoint_returns_stable_first_party_logo_url(tmp_path: Path) -> None:
    resolver = get_token_metadata_resolver()
    resolver.cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                symbol="USDC",
                decimals=6,
                logo_url="https://upstream.example/must-not-leak.png",
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/v1/token", params={"token": token_lower("USDC")})

    assert response.status_code == 200
    assert response.json()["token"] == {
        "chain_id": 1,
        "address": USDC,
        "symbol": "USDC",
        "decimals": 6,
        "logo_url": token_logo_url(chain_id=1, address=USDC),
    }


def test_token_endpoint_uses_original_requested_identity_when_remapped() -> None:
    resolver = get_token_metadata_resolver()
    resolver.cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=REMAPPED_CANONICAL,
                symbol="YFI-LP",
                decimals=18,
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/v1/token", params={"chain_id": 1, "token": REMAPPED_REQUEST})

    assert response.status_code == 200
    payload = response.json()["token"]
    requested = AddressValidator.normalize_address(REMAPPED_REQUEST)
    assert payload["address"] == requested
    assert payload["symbol"] == "YFI-LP"
    assert payload["decimals"] == 18
    assert payload["logo_url"] == token_logo_url(chain_id=1, address=requested)
    with closing(sqlite3.connect(resolver.cache.db_path)) as conn:
        enrolled = conn.execute(
            "SELECT 1 FROM token_logos WHERE chain_id = 1 AND address = ?",
            (requested,),
        ).fetchone()
    assert enrolled == (1,)


def test_token_endpoint_invalid_address_returns_bad_request() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/token",
            params={"chain_id": 1, "token": "0xgggggggggggggggggggggggggggggggggggggggg"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "INVALID_ADDRESS"


def test_public_logo_route_serves_owned_bytes_and_rfc_conditional_requests() -> None:
    resolver = get_token_metadata_resolver()
    override = get_logo_override(chain_id=1, address=WFRAX)
    assert override is not None
    asset = validate_logo_bytes(override.image_bytes, mime_type="image/png")
    resolver.cache.record_logo_success(
        chain_id=1,
        address=WFRAX,
        image_bytes=asset.image_bytes,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        source="override",
        attempted_at=1,
        http_status=None,
    )
    path = f"/token-logos/1/{WFRAX.lower()}"

    with TestClient(app) as client:
        response = client.get(path)
        weak_list = client.get(
            path,
            headers={"If-None-Match": f'"different", W/"{asset.content_hash}"'},
        )
        wildcard = client.get(path, headers={"If-None-Match": "*"})
        malformed_wildcard = client.get(path, headers={"If-None-Match": "*garbage"})
        malformed_tag = client.get(
            path,
            headers={"If-None-Match": f'"{asset.content_hash}"garbage'},
        )

    assert response.status_code == 200
    assert response.content == asset.image_bytes
    assert response.headers["content-type"] == "image/png"
    assert response.headers["etag"] == f'"{asset.content_hash}"'
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    assert response.headers["access-control-allow-origin"] == "*"
    for conditional in (weak_list, wildcard):
        assert conditional.status_code == 304
        assert conditional.headers["etag"] == f'"{asset.content_hash}"'
        assert conditional.headers["cache-control"] == "public, max-age=86400"
        assert conditional.headers["access-control-allow-origin"] == "*"
    assert malformed_wildcard.status_code == 200
    assert malformed_tag.status_code == 200


def test_logo_miss_is_side_effect_free_and_negatively_cached() -> None:
    resolver = get_token_metadata_resolver()
    before = resolver.cache.get_logo_statuses(identities=[(1, USDC)])
    with TestClient(app) as client:
        response = client.get(f"/token-logos/1/{USDC.lower()}")
    after = resolver.cache.get_logo_statuses(identities=[(1, USDC)])

    assert response.status_code == 404
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    assert response.headers["access-control-allow-origin"] == "*"
    assert before == after


def test_malformed_logo_identity_is_400_no_store_without_database_read() -> None:
    resolver = get_token_metadata_resolver()

    def unexpected_read(*, chain_id: int, address: str) -> None:
        del chain_id, address
        raise AssertionError("malformed input must not read SQLite")

    resolver.cache.get_logo_asset = unexpected_read  # type: ignore[method-assign]
    with TestClient(app) as client:
        bad_chain = client.get(f"/token-logos/0/{USDC}")
        bad_address = client.get("/token-logos/1/not-an-address")
        too_large_chain = client.get(f"/token-logos/{1 << 256}/{USDC}")

    for response in (bad_chain, bad_address, too_large_chain):
        assert response.status_code == 400
        assert response.headers["cache-control"] == "no-store"


def test_well_formed_chain_id_above_sqlite_range_is_a_cacheable_miss() -> None:
    with TestClient(app) as client:
        response = client.get(f"/token-logos/{1 << 63}/{USDC}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "public, max-age=300"


def test_logo_route_openapi_is_explicitly_unauthenticated_and_metrics_are_normalized() -> None:
    with TestClient(app) as client:
        client.get(f"/token-logos/1/{USDC}")
        schema = client.get("/openapi.json").json()
        metrics = client.get("/metrics").text

    operation = schema["paths"]["/token-logos/{chain_id}/{address}"]["get"]
    assert operation["security"] == []
    assert 'endpoint="/token-logos/{chain_id}/{address}",method="GET"' in metrics
    assert USDC not in metrics


def test_logo_route_does_not_use_v1_auth_or_anonymous_limiter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_KEY_UNAUTH_ACCESS_ENABLED", "false")
    from price_api.tests.e2e.helpers import clear_singletons

    clear_singletons()
    with TestClient(app) as client:
        first = client.get(f"/token-logos/1/{USDC}")
        second = client.get(f"/token-logos/1/{USDC}")

    assert first.status_code == 404
    assert second.status_code == 404


def test_openapi_includes_token_endpoint() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert "/v1/token" in schema["paths"]
    assert (
        schema["paths"]["/v1/token"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/TokenResponse"
    )


def test_no_upstream_url_is_persisted_in_clean_metadata_table() -> None:
    resolver = get_token_metadata_resolver()
    resolver.cache.upsert_many(
        [
            TokenMetadata(
                chain_id=1,
                address=USDC,
                logo_url="https://provider.example/logo.png",
            )
        ]
    )
    with closing(sqlite3.connect(resolver.cache.db_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(token_metadata)")}
    assert "logo_url" not in columns

# Logo URL Fix

## Problem

Broken token logo URLs become sticky in cache. Root causes:

1. Unverified request-path selection — first candidate returned without validation
2. Speculative URLs persisted as cache truth
3. Multiple provider logo candidates lost (last-writer-wins)
4. SmolDapp too early in fallback order
5. Legacy cached SmolDapp GitHub URLs (`raw.githubusercontent.com/SmolDapp/...`) stuck forever

## Fix (implemented)

### Cache semantics changed

`token_metadata.logo_url` now means **verified only**:

- `logo_status="unknown"`: logo_url is `NULL` in cache, not a speculative URL
- `logo_status="valid"`: logo_url is a verified, returnable URL
- `logo_status="invalid"`: no valid candidate found in last verification pass

Added `logo_source` column to track where a verified logo came from.

### Fallback order fixed

SmolDapp demoted to last. New order:

1. provider logos (all, not just one)
2. cached
3. synced token-list sources (currently CoinGecko)
4. yearn/tokenAssets
5. trustwallet
6. smoldapp

### Multiple provider logo candidates preserved

`collect_provider_logo_urls()` in `policy.py` builds a per-token list of all provider-supplied logo URLs in request order, deduped. These are passed through the full pipeline.

### Request-path behavior

- `valid` (fresh): return cached logo_url
- `valid` (stale, >14 days): treat as unknown, trigger reverification
- `invalid` (fresh): return `null`
- `invalid` (stale, >2 days): treat as unknown, trigger reverification
- `unknown`: return first provider logo URL ephemerally (not persisted), or `null` if no provider logo

Provider-supplied logos are returned in the API response but never written to cache unless verified. Static fallbacks (yearn, trustwallet, smoldapp) are never returned unverified.

### Background verification

When a token has `logo_status="unknown"`, the resolver fires an `asyncio.create_task` to verify candidates in the background. Deduplication via an in-memory `_pending_verification` set prevents N-requests-trigger-N-jobs.

The background task:
1. Builds the full candidate list (provider + static fallbacks)
2. Probes each candidate (HEAD then GET)
3. Validates image response (content-type or magic bytes)
4. Persists the result (`valid` or `invalid`) to cache

### Shared verification module

`token_price_agg/token_metadata/logo_verifier.py` owns:

- Candidate probing (HEAD → GET)
- Image validity checks (content-type + magic bytes: PNG, JPEG, GIF, RIFF, SVG)
- SSRF protections (https-only, reject localhost/private/link-local IPs)
- Short timeouts (2s), max 3 redirects, 1MB cap

The CLI tool (`tools/verify_logo.py`) delegates to this shared module.

### Legacy URL scrub

On cache init, rows matching `logo_url LIKE 'https://raw.githubusercontent.com/SmolDapp/%'` are reset:

- `logo_url = NULL`
- `logo_status = 'unknown'`
- `logo_source = NULL`
- `logo_checked_at = NULL`
- `logo_http_status = NULL`

## Files changed

- `token_price_agg/core/models.py` — added `logo_source` field
- `token_price_agg/token_metadata/logo_urls.py` — `provider_logo_urls: list[str]`, SmolDapp last
- `token_price_agg/token_metadata/logo_sources.py` — synced token-list sources, currently CoinGecko
- `token_price_agg/token_metadata/policy.py` — `collect_provider_logo_urls()`, staleness check, ephemeral provider URLs
- `token_price_agg/token_metadata/cache.py` — `logo_source` column, `scrub_legacy_smoldapp_urls()`
- `token_price_agg/token_metadata/logo_verifier.py` — new shared verification module with SSRF protection
- `token_price_agg/token_metadata/resolver.py` — don't persist unverified, background verification with dedup
- `token_price_agg/tools/verify_logo.py` — delegates to shared verifier
- `token_price_agg/tools/refresh_logo_sources.py` — refreshes synced token-list sources on demand

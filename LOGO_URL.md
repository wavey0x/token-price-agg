# Logo URL Fix Plan

## Problem

We are sometimes returning and persisting broken token logo URLs. Example:

- token: `0x4e3fbd56cd56c3e72c1403e103b45db9da5b9d2b`
- bad logo URL:
  `https://raw.githubusercontent.com/SmolDapp/tokenAssets/main/tokens/1/0x4e3fbd56cd56c3e72c1403e103b45db9da5b9d2b/logo-128.png`

That should not be a sticky result. If a candidate 404s, we should try the next candidate, and SmolDapp should be the last static fallback, not an early default.

One important observation: the current repo does **not** generate that exact SmolDapp GitHub URL anymore. Current code builds `https://assets.smold.app/...` URLs in `token_price_agg/token_metadata/logo_urls.py`. That strongly suggests we also have a **stale cached legacy logo URL** problem, not just a bad live selection problem.

## Current Behavior

### 1. Request-path logo selection is optimistic, not validated

`token_price_agg/token_metadata/policy.py` currently does this for `logo_status="unknown"`:

- build candidates
- pick the first candidate
- return it in the API response

There is no request-path URL validation before returning that URL.

### 2. We persist speculative URLs as cache truth

`token_price_agg/token_metadata/resolver.py` always calls `upsert_many(...)` on the resolved metadata, including the `logo_url` chosen for the response.

That means an unverified fallback can become sticky in SQLite even when `logo_status` is still `unknown`.

### 3. Candidate order is too optimistic about SmolDapp

Current candidate order in `token_price_agg/token_metadata/logo_urls.py` is:

1. provider
2. cached
3. smoldapp
4. yearn/tokenAssets
5. trustwallet

This is wrong for the desired behavior:

- SmolDapp is too early
- a cached speculative SmolDapp URL can outrank better static fallbacks

### 4. We only keep one provider logo hint

`token_price_agg/token_metadata/policy.py` collapses provider metadata into a single `TokenMetadata` hint per token.

That means:

- if provider A gives a broken logo URL
- and provider B gives a good logo URL

we do **not** have a proper "try the next provider logo" chain. We lose the alternative before logo selection even starts.

### 5. Manual verification is separate from request behavior

`token_price_agg/tools/verify_logo.py` does real validation:

- `HEAD`, then `GET`
- checks HTTP status
- checks `content-type` / image signatures

But this logic is only in the manual tool today. The hot path does not use it, and the verifier does not know about all provider candidates seen during a real request.

### 6. Negative cache is too coarse

Today `logo_status="invalid"` means "return `null` for this token".

That is acceptable only if we have already tried the full candidate set. It is not a good model for:

- one bad candidate among several good ones
- sources changing over time
- providers starting to supply a logo later

## Root Causes

The bug is not one thing. It is a combination of:

1. unverified request-path selection
2. persisting unknown/speculative URLs
3. losing multiple provider logo candidates
4. SmolDapp being too early in fallback order
5. no migration/scrub for legacy cached SmolDapp GitHub URLs

## Desired End State

The service should behave like this:

1. `logo_url` stored in cache should mean "verified canonical logo URL", not "last URL we guessed".
2. All provider-supplied logo URLs seen in a request should be preserved as candidates in provider precedence order.
3. Static fallbacks should only run after provider candidates.
4. SmolDapp should be the last static fallback.
5. Broken URLs should not become sticky.
6. Legacy cached SmolDapp GitHub URLs should be scrubbed or revalidated.
7. Validation should be safe and bounded. If we automatically fetch arbitrary provider logo URLs, we need SSRF protections and tight timeouts.

## Recommended Fix

## Phase 1: Stop Making the Cache Worse

This is the minimum safe fix and should land first.

### A. Change cache semantics

Treat `token_metadata.logo_url` as **verified-only**.

For `logo_status="unknown"`:

- do not persist speculative candidate URLs into `logo_url`
- persist only metadata fields that are actually known (`symbol`, `decimals`, etc.)

Recommended addition:

- add `logo_source` to the cache row so we know where a verified logo came from

### B. Demote SmolDapp to last fallback

Change static fallback ordering to:

1. provider logos
2. yearn/tokenAssets
3. trustwallet
4. smoldapp

This keeps provider logos first and pushes SmolDapp to the last line of defense.

### C. Collect all provider logo candidates

Do not collapse provider logo metadata into one value too early.

Add a dedicated candidate collector that builds:

- all provider logo URLs for a token
- in request/provider precedence order
- deduped

This should happen before any static fallbacks are appended.

### D. Scrub legacy cached URLs

We should run a one-time migration/backfill that resets clearly stale legacy SmolDapp GitHub URLs, for example:

- `logo_url LIKE 'https://raw.githubusercontent.com/SmolDapp/%'`

Suggested migration behavior:

- set `logo_url = NULL`
- set `logo_status = 'unknown'`
- clear `logo_checked_at`
- clear `logo_http_status`

This prevents old bad data from surviving forever.

## Phase 2: Introduce Shared Verification Logic

Extract the URL validation logic out of `token_price_agg/tools/verify_logo.py` into a shared module, for example:

- `token_price_agg/token_metadata/logo_verifier.py`

That shared module should own:

- candidate probing
- image validity checks
- short timeout policy
- redirect handling
- bounded response size checks if needed

The CLI tool should call the shared verifier instead of owning its own private copy.

## Phase 3: Background Verification, Not Hot-path Fetching

The cleanest end state is:

- request path returns only a cached `valid` logo URL
- if there is no verified logo yet, it returns `null`
- the service asynchronously verifies candidates and fills the cache

Why this is better:

- no broken fallback URL is returned to clients
- no extra external logo fetch latency is added to `/v1/price` or `/v1/quote`
- the cache becomes authoritative

Recommended flow:

1. request resolves token metadata
2. if `logo_status="valid"` and still fresh, return cached `logo_url`
3. else enqueue a background verification job for that token
4. return `null` for now, or optionally an unverified provider URL during transition

### Transition option

If we want a softer rollout:

- continue returning unverified provider logo URLs ephemerally
- but never persist them unless verified
- do not return unverified SmolDapp/yearn/trustwallet fallbacks

This is less strict than the end state, but already avoids the worst sticky-404 behavior.

## Phase 4: Revalidation Policy

We should not treat logo validity as permanent.

Recommended policy:

- `valid` logos: recheck on a long TTL, e.g. 7-30 days
- `invalid` results: recheck on a shorter TTL, e.g. 1-3 days
- `unknown`: schedule verification soon, but do not persist guessed URLs

This avoids:

- permanent suppression after one failure
- permanent trust after an old successful check

## Schema / Model Changes

Recommended additions to `token_metadata`:

- `logo_source`
- optionally `logo_next_check_at`

Recommended semantic changes:

- `logo_url`: verified canonical URL only
- `logo_status`:
  - `valid`: `logo_url` is verified and returnable
  - `invalid`: no valid candidate found in the last full verification pass
  - `unknown`: not verified yet or verification pending

No new `stale` enum is strictly required if TTL is derived from timestamps.

## Security / Safety Requirements

If we automatically verify provider-supplied logo URLs, we need a tighter fetch policy than we have today in the CLI tool.

At minimum:

- only allow `https`
- reject localhost / private / link-local destinations after DNS resolution
- cap redirects
- keep short timeouts
- cap downloaded bytes
- use a dedicated user agent

This matters because provider logo URLs are untrusted input.

## Proposed Code Touchpoints

- `token_price_agg/token_metadata/logo_urls.py`
  - reorder static fallbacks
  - split provider-candidate collection from static candidate generation
- `token_price_agg/token_metadata/policy.py`
  - stop selecting/persisting speculative unknown URLs as cache truth
  - preserve multiple provider logo candidates
- `token_price_agg/token_metadata/resolver.py`
  - only persist verified logo URLs
  - optionally enqueue background verification
- `token_price_agg/token_metadata/cache.py`
  - add `logo_source`
  - add optional recheck timestamp field
- `token_price_agg/tools/verify_logo.py`
  - switch to shared verifier module
- new module:
  - `token_price_agg/token_metadata/logo_verifier.py`

## Test Plan

Add or update tests for:

1. provider logo candidates preserve multiple provider URLs in order
2. SmolDapp is last in fallback order
3. `logo_status="unknown"` does not persist speculative `logo_url`
4. a cached legacy SmolDapp GitHub URL is scrubbed/reset by migration
5. when first provider logo is invalid and second is valid, verifier selects the second
6. when no verified candidate exists, request path returns `null` or transition-mode provider logo only
7. `invalid` entries are rechecked after TTL
8. unsafe logo URLs are rejected by the verifier

## Rollout Plan

1. Land Phase 1 plus migration:
   - stop persisting unknown logo URLs
   - reorder fallbacks
   - preserve multiple provider logo candidates
   - scrub legacy SmolDapp GitHub URLs
2. Land shared verifier module
3. Add bounded async/background verification
4. Backfill hot tokens with the verifier
5. Update API docs to clarify that cached `logo_url` is verified, not best-effort guessed

## Recommendation

If we want the highest-confidence fix, the key decision is this:

`logo_url` in cache should represent a verified canonical asset, not a best-effort guess.

Everything else gets simpler once that contract is true.

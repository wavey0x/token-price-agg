# Token Price Agg Runbook

## Local Run

```bash
uvicorn token_price_agg.app.main:app --reload
```

## Local Docs

```bash
uv run --extra docs mkdocs serve
```

Static build:

```bash
uv run --extra docs mkdocs build
```

## Environment

Required/important env vars:
- `CHAIN_IDS` (default: `1`)
- `RPC_URLS` (enables best-effort `use_underlying=true` vault resolution)
- `LIFI_API_KEY` (required to enable `lifi`)
- `ENSO_API_KEY` (required to enable `enso`)
- `TOKEN_METADATA_DB_PATH` (default: `data/token_metadata.sqlite3`)
- `PROVIDER_REQUEST_TIMEOUT_MS` (default: `800`)
- `PROVIDER_MAX_RETRIES` (default: `0`)
- `PROVIDERS_ENABLED` (default: `defillama,curve,odos,lifi,enso`)
- `PRICE_PROVIDER_PRIORITY` (optional default precedence)
- `QUOTE_PROVIDER_PRIORITY` (optional default precedence)
- `API_KEY_AUTH_ENABLED` (default: `false`)
- `API_KEY_DB_PATH` (default: `data/api_keys.sqlite3`)
- `API_KEY_RATE_LIMIT_RPM` (default: `300`)
- `API_KEY_UNAUTH_ACCESS_ENABLED` (default: `true`)
- `API_KEY_UNAUTH_MIN_INTERVAL_SECONDS` (default: `1`)

Config file:
- `config/app.toml`
- precedence: env vars > `.env` > `config/app.toml` > defaults

Deadline model:
- price aggregate deadline = `provider_request_timeout_ms + 100ms`
- quote aggregate deadline = `provider_request_timeout_ms + 300ms`

Observability env vars:
- `LOG_FORMAT` (`json` or `text`, default `json`)
- `METRICS_ENABLED` (`true`/`false`, default `true`)
- `ENABLE_READINESS_STRICT` (`true`/`false`, default `false`)

## Runtime Endpoints

- `GET /v1/health` liveness (`/v1/*` uses authenticated tier and optional unauthenticated tier when `API_KEY_AUTH_ENABLED=true`)
- `GET /v1/ready` readiness (`/v1/*` uses authenticated tier and optional unauthenticated tier when enabled)
- `GET /v1/providers` provider capabilities and availability (`/v1/*` uses authenticated tier and optional unauthenticated tier when enabled)
- `GET /metrics` Prometheus scrape endpoint
- `GET /v1/price` aggregated price response (`/v1/*` uses authenticated tier and optional unauthenticated tier when enabled)
- `GET /v1/quote` aggregated quote response (`/v1/*` uses authenticated tier and optional unauthenticated tier when enabled)

Auth behavior (when enabled):
- send `Authorization: Bearer <api_key>` for authenticated tier
- valid API keys are rate-limited by `API_KEY_RATE_LIMIT_RPM` unless per-key override is set via CLI
- missing authorization is allowed only when `API_KEY_UNAUTH_ACCESS_ENABLED=true` and is limited to one request per `API_KEY_UNAUTH_MIN_INTERVAL_SECONDS` per client IP
- invalid/revoked/expired key returns `401` with:
  - `WWW-Authenticate: Bearer`
  - `{"detail":{"code":"UNAUTHORIZED","message":"..."}}`
- rate-limited responses return `429` with:
  - `Retry-After`
  - `X-RateLimit-Limit`
  - `X-RateLimit-Remaining`
  - `X-RateLimit-Reset`
  - `{"detail":{"code":"RATE_LIMITED","message":"..."}}`

## Request Contract

`GET /v1/price` query params:
- `chain_id` (int, required)
- `token` (EVM address, required)
- `providers` (optional, repeated or comma-separated)
- `use_underlying` (bool, optional, default `false`)

`GET /v1/quote` query params:
- `chain_id` (int, required)
- `token_in` / `token_out` (EVM addresses, required)
- `amount_in` (positive integer string, required)
- `providers` (optional, repeated or comma-separated)
- `include_route` (bool, optional, default `false`)
- `use_underlying` (bool, optional, default `false`)

Providers query examples:
- repeated: `providers=curve&providers=defillama`
- csv: `providers=curve,defillama`

## Response Contract

Shared fields:
- `request_id`, `chain_id`, `provider_order`, `summary`

Price response:
- `token`: request token metadata
- `price_data`: top-level selected result (`null` if no successful provider)
- `providers`: keyed map of provider results by provider ID
- `price_data.price` and `providers.*.price` are normalized USD prices
- with `use_underlying=true`, price is vault-share USD price
  (`underlying_price * price_per_share`)
- price summary fields: `high_price`, `low_price`, `median_price`, `deviation_bps`
- shared summary fields: `requested_providers`, `successful_providers`, `failed_providers`

Quote response:
- `token_in`, `token_out`: request token metadata
- `quote`: top-level selected result (`null` if no successful provider)
- `providers`: keyed map of provider results by provider ID
- quote summary fields: `high_amount_out`, `low_amount_out`, `median_amount_out`

`use_underlying=true` behavior (best effort):
- If vault resolution succeeds, vault legs are converted to underlying for provider calls.
- For vault output legs, response `amount_out`/`amount_out_min` are converted back to shares.
- If vault detection/web3 fails or no vault is detected, request proceeds unchanged.

Selection semantics:
1. request `providers` order (if present)
2. else `PRICE_PROVIDER_PRIORITY` / `QUOTE_PROVIDER_PRIORITY`
3. then remaining selected providers in deterministic order (alphabetical provider ID)

Provider failures do not fail whole requests:
- provider-level failures are returned under `providers`
- endpoint returns `200` unless request validation/domain validation fails

## Token Metadata Enrichment

Metadata resolution order:
1. local SQLite cache
2. provider response token metadata
3. on-chain ERC20 metadata via multicall (RPC required)
4. logo candidates (`provider -> SmolDapp -> yearn/tokenAssets -> TrustWallet`) without request-path URL checks

Logo URL state behavior:
- `logo_status=valid`: API returns stored verified URL.
- `logo_status=invalid`: API returns `logo_url=null`.
- `logo_status=unknown`: API returns first candidate URL (best-effort).

Manual logo verification command:

```bash
uv run python -m token_price_agg.tools.verify_logo --chain-id 1 --token 0x22222222aEA0076fCA927a3f44dc0B4FdF9479D6
```

This command force-refreshes one token and persists valid/invalid logo status.

SQLite runtime files:
- `data/*.sqlite3` are local runtime state and should remain untracked.
- If `data/token_metadata.sqlite3` is tracked in a branch, untrack once:
  `git rm --cached data/token_metadata.sqlite3`

## Docs Deployment

Docs can be deployed directly to Vercel as a static site:
- install: `uv sync --frozen --extra docs`
- build: `uv run --extra docs mkdocs build`
- output: `docs/site`

## Logging

Structured JSON by default.

Primary fields:
- `ts`, `level`, `logger`, `msg`
- `request_id`, `path`, `method`, `status_code`, `latency_ms`
- `provider`, `provider_status`, `error_code`
- `env`, `version`

Correlation:
- send `X-Request-ID`
- service echoes `X-Request-ID`

## Metrics

Core metrics:
- `token_price_agg_http_requests_total`
- `token_price_agg_http_request_latency_seconds`
- `token_price_agg_http_inflight_requests`
- `token_price_agg_auth_total`
- `token_price_agg_rate_limit_total`
- `token_price_agg_provider_calls_total`
- `token_price_agg_provider_call_latency_seconds`
- `token_price_agg_provider_available`
- `token_price_agg_partial_responses_total`
- `token_price_agg_all_failed_responses_total`
- `token_price_agg_vault_resolution_total`
- `token_price_agg_vault_resolution_latency_seconds`

Low-cardinality policy:
- allowed labels: endpoint, method, provider, operation, status
- disallowed labels: token addresses, request_id, raw error text

## Useful PromQL

5xx ratio:

```promql
sum(rate(token_price_agg_http_requests_total{status_class="5xx"}[5m]))
/
clamp_min(sum(rate(token_price_agg_http_requests_total[5m])), 1)
```

p95 `/v1/price` latency:

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(token_price_agg_http_request_latency_seconds_bucket{endpoint="/v1/price",method="GET"}[5m])
  )
)
```

Provider timeout rate:

```promql
sum by (provider) (
  rate(token_price_agg_provider_calls_total{status="timeout"}[5m])
)
```

## Quick Verification

If auth is enabled:

```bash
API_KEY="$(api-key generate --label smoke --json | jq -r '.key')"
```

Health:

```bash
curl -s -H "Authorization: Bearer ${API_KEY}" http://localhost:8000/v1/health
```

Readiness:

```bash
curl -s -H "Authorization: Bearer ${API_KEY}" http://localhost:8000/v1/ready
```

Metrics:

```bash
curl -s http://localhost:8000/metrics
```

Price request:

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=defillama'
```

Quote request:

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/quote?chain_id=1&token_in=0xd533a949740bb3306d119cc777fa900ba034cd52&token_out=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&amount_in=1000000000000000000&providers=curve'
```

Expected behavior:
- request tokens are checksummed in response
- `provider_order` is present
- `price`/`quote` may be `null` when all providers fail

## Real Token Matrix and Live Smoke

Token/pair matrix config:
- `token_price_agg/tests/fixtures/ethereum_tokens.py`

Manual live smoke:

```bash
uv run python token_price_agg/tests/manual/smoke_get_live.py --base-url http://localhost:8000
```

With auth enabled:

```bash
uv run python token_price_agg/tests/manual/smoke_get_live.py \
  --base-url http://localhost:8000 \
  --api-key "${API_KEY}"
```

Optional filters:

```bash
uv run python token_price_agg/tests/manual/smoke_get_live.py \
  --price-symbols CRV,CVX,USDC,YFI \
  --quote-symbols CRV,CVX,USDC,YFI \
  --providers curve,defillama
```

## Test and Typecheck

```bash
uv run --extra dev pytest
uv run ruff check .
uv run mypy .
```

## Common Failure Modes

- `INVALID_ADDRESS`: malformed token address
- `UNAUTHORIZED`: invalid/revoked/expired bearer token (or missing bearer when `API_KEY_UNAUTH_ACCESS_ENABLED=false`)
- `RATE_LIMITED`: API key exceeded per-minute budget or anonymous tier exceeded per-second budget
- provider status `invalid_request` + `missing_api_key`
- provider status `timeout`/`upstream_error`
- readiness `not_ready` with reason `no_available_providers` in strict mode

## Triage Checklist

1. Confirm `/v1/ready`.
2. Check 5xx ratio and p95 latency.
3. Check provider availability gauges.
4. Filter logs by `request_id`.
5. Verify upstream key configuration and network reachability.

## API Key Operations

Create key:

```bash
api-key generate
```

List keys:

```bash
api-key list
api-key list --all
```

Delete key:

```bash
api-key delete <key_id>
```

Set per-key rate limit override (rpm):

```bash
api-key set-rate-limit <key_id> 120
```

## Deployment Notes for Probes

When `API_KEY_AUTH_ENABLED=true` and `API_KEY_UNAUTH_ACCESS_ENABLED=false`, Kubernetes `httpGet`
probes on `/v1/health` and `/v1/ready` will fail without auth headers. Update probes to an
authenticated strategy (for example an `exec` probe that includes `Authorization: Bearer ...`) or
enable unauthenticated access with low RPS for probe traffic.

# Operations

## Configuration

Use `config/app.toml` as the primary config source for non-secrets.

Common TOML keys:

- `[chains].ids` (default `[1]`)
- `[rpc].urls` (enables best-effort `use_underlying=true` vault resolution)
- `[timeouts].provider_request_timeout_ms`
- `[timeouts].provider_max_retries`
- `[concurrency].provider_fanout_per_request`
- `[concurrency].provider_global_limit`
- `[providers].enabled`
- `[providers].price_priority`
- `[providers].quote_priority`
- `[security].api_key_auth_enabled` (default `false`)
- `[security].api_key_db_path` (default `data/api_keys.sqlite3`)
- `[security].api_key_rate_limit_rpm` (default `300`)
- `[security].api_key_unauth_access_enabled` (default `true`)
- `[security].api_key_unauth_rate_limit_rps` (default `1`)

Use `.env` for secrets and occasional overrides:

- `LIFI_API_KEY`
- `ENSO_API_KEY`

Optional env overrides:

- `APP_ENV`
- `APP_VERSION` (optional; default `0.1.0`)
- any TOML-backed setting via its env name (for example `RPC_URLS`)

Observability:

- `LOG_FORMAT` (`json` or `text`)
- `METRICS_ENABLED` (`true`/`false`)
- `ENABLE_READINESS_STRICT` (`true`/`false`)

Auth behavior (when enabled):
- valid bearer keys use `api_key_rate_limit_rpm` by default, with optional per-key override via CLI
- missing authorization is allowed at `api_key_unauth_rate_limit_rps` per client IP when `api_key_unauth_access_enabled=true`
- invalid/revoked/expired authorization returns `401`
- `/metrics` remains public
- auth failures return `401` with `WWW-Authenticate: Bearer`
- rate-limit failures return `429` with `Retry-After` and `X-RateLimit-*` headers

## Runtime Checks

```bash
curl -s -H "Authorization: Bearer ${API_KEY}" http://localhost:8000/v1/health
curl -s -H "Authorization: Bearer ${API_KEY}" http://localhost:8000/v1/ready
curl -s http://localhost:8000/metrics
```

## API Key Operations

```bash
api-key generate
api-key list
api-key delete <key_id>
api-key set-rate-limit <key_id> <rpm>
```

## Development Validation

```bash
uv run ruff check .
uv run mypy .
uv run --extra dev pytest
```

## Live Smoke

Uses shared token matrix from `token_price_agg/tests/fixtures/ethereum_tokens.py`.

```bash
uv run python token_price_agg/tests/manual/smoke_get_live.py --base-url http://localhost:8000
```

## Docs on Vercel

Yes, this docs site can run directly on Vercel as a static deployment.

- Install command: `uv sync --frozen --extra docs`
- Build command: `uv run --extra docs mkdocs build`
- Output directory: `docs/site`

Runtime API process (`uvicorn`) is separate from docs hosting.

## Downstream Health Dashboard (Grafana)

Use dashboard JSON:

- `deploy/monitoring/dashboards/token_price_agg_overview.json`

This dashboard is focused on downstream provider health (independent of top-level selection):

- success: `status="ok"`
- failure: `status!="ok"`
- window: `rate(...[12h])`

Top row includes separate gauges for:

- Price Success %
- Price Failure %
- Quote Success %
- Quote Failure %

Prometheus scrape target for single-server local deployment:

- `127.0.0.1:18743`

Validation checklist:

1. Prometheus target is `UP` at `/targets` for `job="token-price-agg"`.
2. Import dashboard JSON into Grafana and select Prometheus datasource.
3. Generate `/v1/price` and `/v1/quote` traffic and verify provider calls increment by `provider,operation,status`.
4. Confirm any non-`ok` outcome increases failure % gauges.

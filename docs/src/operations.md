# Operations

## Configuration

Use `config/app.toml` as the primary config source for non-secrets.

Common TOML keys:

- `[chains].ids` (default `[1]`)
- `[rpc].urls` (required for `is_vault=true`)
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
- `/v1/*` requires `Authorization: Bearer <api-key>`
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
api-key invalidate <key_id>
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

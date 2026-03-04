# Operations

## Environment

Core settings:

- `CHAIN_IDS` (default `1`)
- `RPC_URLS` (needed for `is_vault=true`)
- `LIFI_API_KEY`
- `ENSO_API_KEY`
- `TOKEN_METADATA_DB_PATH`
- `PROVIDER_REQUEST_TIMEOUT_MS`
- `PROVIDER_MAX_RETRIES`
- `PROVIDER_FANOUT_PER_REQUEST`
- `PROVIDER_GLOBAL_LIMIT`
- `PRICE_PROVIDER_PRIORITY`
- `QUOTE_PROVIDER_PRIORITY`
- `API_KEY_AUTH_ENABLED` (default `false`)
- `API_KEY_DB_PATH` (default `data/api_keys.sqlite3`)
- `API_KEY_RATE_LIMIT_RPM` (default `300`)

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

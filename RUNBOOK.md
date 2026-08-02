# Price API Runbook

Operational notes for the token price API deployment.

## Production

- Host: `electro`
- Repo directory on host: `price-api`
- systemd service: `price-api`
- Primary branch: `master`
- Runtime config: `/opt/price-api/config/app.toml` selected by `PRICE_API_CONFIG_FILE`
- Runtime secrets and host-specific overrides: `.env`

Do not commit `.env` or runtime SQLite files under `data/`.

## Change Deployment

Use this process for normal code/config/docs changes.

From local checkout:

```bash
git status
uv run ruff check .
uv run mypy .
uv run --extra dev pytest
```

Run at least one local API smoke path before deployment:

```bash
API_KEY_AUTH_ENABLED=false uv run python - <<'PY'
from fastapi.testclient import TestClient
from price_api.app.main import app

with TestClient(app) as client:
    response = client.get("/v1/health")
    print(response.status_code, response.json())
PY
```

Commit and push:

```bash
git add <changed-files>
git commit -m "<message>"
git push origin master
```

Deploy on `electro`:

```bash
ssh electro
cd price-api
git status
git pull --ff-only
uv sync --frozen
sudo systemctl restart price-api
sudo systemctl status price-api --no-pager
```

Verify after restart:

```bash
curl -sS http://127.0.0.1:8000/v1/health
curl -sS http://127.0.0.1:8000/v1/ready
sudo journalctl -u price-api -n 100 --no-pager
```

If `API_KEY_AUTH_ENABLED=true` and unauthenticated access is disabled, include a valid API key:

```bash
curl -sS -H "Authorization: Bearer ${API_KEY}" http://127.0.0.1:8000/v1/health
curl -sS -H "Authorization: Bearer ${API_KEY}" http://127.0.0.1:8000/v1/ready
```

## Service Operations

Status:

```bash
ssh electro
cd price-api
sudo systemctl status price-api --no-pager
```

Restart:

```bash
ssh electro
cd price-api
sudo systemctl restart price-api
```

Logs:

```bash
ssh electro
cd price-api
sudo journalctl -u price-api -f
```

Recent logs:

```bash
ssh electro
cd price-api
sudo journalctl -u price-api -n 200 --no-pager
```

## Runtime Checks

Health:

```bash
curl -sS http://127.0.0.1:8000/v1/health
```

Readiness:

```bash
curl -sS http://127.0.0.1:8000/v1/ready
```

Providers:

```bash
curl -sS http://127.0.0.1:8000/v1/providers
```

Price smoke:

```bash
curl -sS \
  "http://127.0.0.1:8000/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=defillama"
```

Quote smoke:

```bash
curl -sS \
  "http://127.0.0.1:8000/v1/quote?chain_id=1&token_in=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&token_out=0x6b175474e89094c44da98b954eedeac495271d0f&amount_in=1000000&providers=curve"
```

Metrics:

```bash
curl -sS http://127.0.0.1:8000/metrics
```

Manual live smoke helper:

```bash
uv run python price_api/tests/manual/smoke_get_live.py --base-url http://127.0.0.1:8000
```

With API auth:

```bash
uv run python price_api/tests/manual/smoke_get_live.py \
  --base-url http://127.0.0.1:8000 \
  --api-key "${API_KEY}"
```

## Configuration

Non-secret config belongs in `config/app.toml`.

Secrets and host-specific overrides belong in `.env` on `electro`.

Important env vars:

- `PRICE_API_CONFIG_FILE` (absolute path to the required TOML file when explicitly set)
- `CHAIN_IDS` (default: `1`)
- `RPC_URLS` (enables best-effort `use_underlying=true` vault resolution)
- `LIFI_API_KEY` (required to enable `lifi`)
- `ENSO_API_KEY` (required to enable `enso`)
- `PROVIDERS_ENABLED` (default: `defillama,curve,lifi,enso`)
- `PRICE_PROVIDER_PRIORITY` (optional default precedence)
- `QUOTE_PROVIDER_PRIORITY` (optional default precedence)
- `API_KEY_AUTH_ENABLED` (default: `true`)
- `API_KEY_DB_PATH` (default: `data/api_keys.sqlite3`)
- `API_KEY_RATE_LIMIT_RPM` (default: `300`)
- `API_KEY_UNAUTH_ACCESS_ENABLED` (default: `true`)
- `API_KEY_UNAUTH_MIN_INTERVAL_SECONDS` (default: `1`)
- `TOKEN_METADATA_DB_PATH` (default: `data/token_metadata.sqlite3`)
- `PROVIDER_REQUEST_TIMEOUT_MS` (default: `800`)
- `PROVIDER_MAX_RETRIES` (default: `0`)
- `PROVIDER_HTTP_TRUST_ENV` (default: `false`)

Settings precedence:

1. environment variables
2. `.env`
3. `config/app.toml`
4. code defaults

## API Key Operations

Run from the deployment directory:

```bash
ssh electro
cd price-api
```

Create a consumer API key:

```bash
uv run api-key generate
```

List keys:

```bash
uv run api-key list
uv run api-key list --all
```

Delete key:

```bash
uv run api-key delete <key_id>
```

Set per-key rate limit override:

```bash
uv run api-key set-rate-limit <key_id> 120
```

## Logs And Correlation

Structured JSON logs are the default.

Useful fields:

- `request_id`
- `path`
- `method`
- `status_code`
- `latency_ms`
- `auth_status`
- `auth_reason`
- `api_key_id`
- `provider`
- `operation`
- `provider_status`
- `error_type`

Send `X-Request-ID` on test requests when tracing a single flow:

```bash
curl -sS -H "X-Request-ID: manual-check-$(date +%s)" http://127.0.0.1:8000/v1/health
```

## Common Failure Modes

- `UNAUTHORIZED`: invalid/revoked/expired API key, or missing auth when unauthenticated access is disabled.
- `RATE_LIMITED`: consumer API key exceeded per-minute budget, anonymous tier exceeded budget, or a provider returned 429.
- `SERVICE_OVERLOADED`: global provider admission capacity is exhausted before fan-out.
- provider error `PROVIDER_UNAVAILABLE`: provider is disabled, missing required credentials, circuit-open, or its per-provider lane is full.
- provider error `RATE_LIMITED`: upstream provider rate limit.
- provider error `INTERNAL_TRANSPORT_TIMEOUT`: local HTTP pool could not hand out a connection before `provider_pool_timeout_ms`.
- readiness `not_ready`: check `/v1/ready` response body for `reason`.

## Triage

1. Check service status: `sudo systemctl status price-api --no-pager`.
2. Check readiness: `curl -sS http://127.0.0.1:8000/v1/ready`.
3. Check recent logs: `sudo journalctl -u price-api -n 200 --no-pager`.
4. Check provider availability: `curl -sS http://127.0.0.1:8000/v1/providers`.
5. Check metrics: `curl -sS http://127.0.0.1:8000/metrics`.
6. Verify `.env` contains required upstream keys for enabled providers.
7. If provider transport appears wedged, restart `price-api` and re-run readiness.

## Rollback

Preferred rollback is a normal revert commit from local checkout:

```bash
git revert <bad_commit>
git push origin master
ssh electro
cd price-api
git pull --ff-only
uv sync --frozen
sudo systemctl restart price-api
sudo systemctl status price-api --no-pager
```

Use direct host-side rollback only for urgent incidents, and record the commit restored:

```bash
ssh electro
cd price-api
git log --oneline -5
git checkout <known_good_commit>
uv sync --frozen
sudo systemctl restart price-api
sudo systemctl status price-api --no-pager
```

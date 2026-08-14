# Price API Runbook

Operational notes for the token price API deployment.

## Production

- Host: `electro`
- Repo directory on host: `/home/wavey/price-api`
- systemd service: `price-api`
- Primary branch: `master`
- Service endpoint: `http://127.0.0.1:18743`
- Runtime config: `/home/wavey/price-api/config/app.toml`
- Runtime secrets and host-specific overrides: `/home/wavey/price-api/.env`
- Service environment: `/home/wavey/price-api/venv`

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
cd /home/wavey/price-api
git status
git pull --ff-only
UV_PROJECT_ENVIRONMENT=venv uv sync --frozen
sudo systemctl restart price-api
sudo systemctl status price-api --no-pager
```

Verify after restart:

```bash
curl -sS http://127.0.0.1:18743/v1/health
sleep 6
curl -sS http://127.0.0.1:18743/v1/ready
sudo journalctl -u price-api -n 100 --no-pager
```

## Token Logo Clean-Schema Cutover

The token-logo ownership release intentionally rejects the legacy token metadata cache. Use this
one-time cutover instead of the normal restart procedure. Keep Nginx live; the image route needs no
proxy-specific configuration.

1. Record the deployed revision and health/readiness. Export only deduplicated identities—never old
   image URLs or verification state—from both operational databases:

```bash
cd /home/wavey/price-api
git rev-parse HEAD
curl -fsS http://127.0.0.1:18743/v1/health
{
  sqlite3 data/token_metadata.sqlite3 \
    "SELECT chain_id || ',' || address FROM token_metadata;"
  sqlite3 /home/wavey/auctionscan/backend/data/auctionscan.sqlite3 \
    "SELECT chain_id || ',' || token_address FROM tokens;"
} | sort -u > data/token-logo-identities.csv
```

2. Deploy the committed code and locked environment. Stop the service, validate and archive the old
   cache, then enroll while the service remains stopped:

```bash
sudo systemctl stop price-api
sqlite3 data/token_metadata.sqlite3 'PRAGMA quick_check;'
archive="data/token_metadata.sqlite3.pre-token-logos.$(date -u +%Y%m%dT%H%M%SZ)"
mv data/token_metadata.sqlite3 "$archive"
venv/bin/token-logo-prewarm --db-path data/token_metadata.sqlite3 enroll \
  --input data/token-logo-identities.csv --confirm-service-stopped
```

Record `enrollment_started_at_ms` from the JSON output. Start the service, verify health/readiness,
then wait read-only for one bounded pass:

```bash
sudo systemctl start price-api
curl -fsS http://127.0.0.1:18743/v1/health
sleep 6
curl -fsS http://127.0.0.1:18743/v1/ready
venv/bin/token-logo-prewarm --db-path data/token_metadata.sqlite3 wait \
  --input data/token-logo-identities.csv \
  --started-at-ms <enrollment_started_at_ms> \
  --deadline-seconds 1800
```

3. Verify a present image (`200`, exact raster MIME/body, ETag, one-day cache and security/CORS
   headers), conditional `304`, a well-formed missing image (`404` with five-minute cache), and
   malformed input (`400` with `no-store`). Verify token, price, and quote responses contain only
   `https://prices.wavey.info/token-logos/...` URLs. Delete the temporary identity file after the
   prewarm record is captured; retain the archived cache through the observation window.

Rollback is coordinated: stop `price-api`, restore the previous code revision and locked
environment, replace the new cache with the archived legacy cache, then start and verify. Never run
new-schema code against the legacy cache or old code against the new cache.

If `API_KEY_AUTH_ENABLED=true` and unauthenticated access is disabled, include a valid API key:

```bash
curl -sS -H "Authorization: Bearer ${API_KEY}" http://127.0.0.1:18743/v1/health
curl -sS -H "Authorization: Bearer ${API_KEY}" http://127.0.0.1:18743/v1/ready
```

## Service Operations

Status:

```bash
ssh electro
cd /home/wavey/price-api
sudo systemctl status price-api --no-pager
```

Restart:

```bash
ssh electro
cd /home/wavey/price-api
sudo systemctl restart price-api
```

Logs:

```bash
ssh electro
cd /home/wavey/price-api
sudo journalctl -u price-api -f
```

Recent logs:

```bash
ssh electro
cd /home/wavey/price-api
sudo journalctl -u price-api -n 200 --no-pager
```

## Runtime Checks

Health:

```bash
curl -sS http://127.0.0.1:18743/v1/health
```

Readiness:

```bash
curl -sS http://127.0.0.1:18743/v1/ready
```

Providers:

```bash
curl -sS http://127.0.0.1:18743/v1/providers
```

Price smoke:

```bash
curl -sS \
  "http://127.0.0.1:18743/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=defillama"
```

Quote smoke:

```bash
curl -sS \
  "http://127.0.0.1:18743/v1/quote?chain_id=1&token_in=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&token_out=0x6b175474e89094c44da98b954eedeac495271d0f&amount_in=1000000&providers=curve"
```

Metrics:

```bash
curl -sS http://127.0.0.1:18743/metrics
```

Manual live smoke helper:

```bash
uv run python price_api/tests/manual/smoke_get_live.py --base-url http://127.0.0.1:18743
```

With API auth:

```bash
uv run python price_api/tests/manual/smoke_get_live.py \
  --base-url http://127.0.0.1:18743 \
  --api-key "${API_KEY}"
```

## Configuration

Non-secret config belongs in `config/app.toml`.

Secrets and host-specific overrides belong in `.env` on `electro`.

Important env vars:

- `PRICE_API_CONFIG_FILE` (absolute path to the required TOML file when explicitly set)
- `CHAIN_IDS` (default: `1`)
- `RPC_URLS` (required for `use_underlying=true`; resolution failures fail closed per provider)
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
cd /home/wavey/price-api
```

Create a consumer API key:

```bash
venv/bin/api-key generate
```

List keys:

```bash
venv/bin/api-key list
venv/bin/api-key list --all
```

Delete key:

```bash
venv/bin/api-key delete <key_id>
```

Set per-key rate limit override:

```bash
venv/bin/api-key set-rate-limit <key_id> 120
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
curl -sS -H "X-Request-ID: manual-check-$(date +%s)" http://127.0.0.1:18743/v1/health
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
2. Check readiness: `curl -sS http://127.0.0.1:18743/v1/ready`.
3. Check recent logs: `sudo journalctl -u price-api -n 200 --no-pager`.
4. Check provider availability: `curl -sS http://127.0.0.1:18743/v1/providers`.
5. Check metrics: `curl -sS http://127.0.0.1:18743/metrics`.
6. Verify `.env` contains required upstream keys for enabled providers.
7. If provider transport appears wedged, restart `price-api` and re-run readiness.

## Rollback

Preferred rollback is a normal revert commit from local checkout:

```bash
git revert <bad_commit>
git push origin master
ssh electro
cd /home/wavey/price-api
git pull --ff-only
UV_PROJECT_ENVIRONMENT=venv uv sync --frozen
sudo systemctl restart price-api
sudo systemctl status price-api --no-pager
```

Use direct host-side rollback only for urgent incidents, and record the commit restored:

```bash
ssh electro
cd /home/wavey/price-api
git log --oneline -5
git checkout <known_good_commit>
UV_PROJECT_ENVIRONMENT=venv uv sync --frozen
sudo systemctl restart price-api
sudo systemctl status price-api --no-pager
```

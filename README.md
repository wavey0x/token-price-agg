# token-price-agg

Ethereum token price/quote aggregator with plugin-style providers.

## Ubuntu Server Install (systemd)

These steps assume Ubuntu 22.04+ and deploy to `/opt/token-price-agg`.

### 1) Install OS packages

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3 python3-venv
```

### 2) Install `uv`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
uv --version
```

### 3) Clone and install project dependencies

```bash
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
cd /opt
git clone <YOUR_REPO_URL> token-price-agg
cd token-price-agg
uv sync --frozen
```

### 4) Configure environment

Use TOML for non-secret runtime config (timeouts, providers, RPC URLs, auth/rate limits).

Review and edit:

```bash
$EDITOR config/app.toml
```

Example RPC configuration in TOML:

```toml
[rpc]
urls = ["https://mainnet.example-rpc"]
```

Create runtime env file for secrets and optional overrides:

```bash
cp .env.example .env
```

Minimum fields to review in `.env`:

- `LIFI_API_KEY` and `ENSO_API_KEY` (optional, required only if enabling those providers)

Notes:
- `APP_VERSION` is optional (default is `0.1.0`).
- For production, keep non-secrets in `config/app.toml`; use `.env` mainly for secrets and one-off overrides.

### 5) Initialize API keys (if auth enabled)

```bash
./.venv/bin/api-key generate --label "server-default"
./.venv/bin/api-key list
```

Save the generated key securely. It is shown once at creation time.

### 6) Install systemd service

Use the provided unit:

```bash
sudo cp deploy/systemd/token-price-agg.service /etc/systemd/system/token-price-agg.service
```

If needed, edit these fields in the unit file:

- `WorkingDirectory=/opt/token-price-agg`
- `EnvironmentFile=/opt/token-price-agg/.env`
- `ExecStart=/opt/token-price-agg/.venv/bin/uvicorn ...`
- `User=www-data` and `Group=www-data`

Grant service user access to app files:

```bash
sudo chown -R www-data:www-data /opt/token-price-agg
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now token-price-agg
sudo systemctl status token-price-agg
```

### 7) Verify service

If auth is enabled:

```bash
API_KEY="<your_api_key>"
curl -s -H "Authorization: Bearer ${API_KEY}" http://127.0.0.1:8000/v1/health
curl -s -H "Authorization: Bearer ${API_KEY}" "http://127.0.0.1:8000/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=defillama"
```

Metrics stays unauthenticated:

```bash
curl -s http://127.0.0.1:8000/metrics
```

Logs:

```bash
sudo journalctl -u token-price-agg -f
```

### 8) Optional: reverse proxy/firewall

- Put the service behind Nginx/Caddy and expose only 80/443 publicly.
- Keep port `8000` bound to localhost or restricted network where possible.
- If `API_KEY_AUTH_ENABLED=true`, ensure any external health/readiness checks include a bearer token for `/v1/*`.

## Local Run

```bash
uvicorn token_price_agg.app.main:app --reload
```

## Tests

```bash
uv run --extra dev pytest
```

## Docs

Serve local docs site:

```bash
uv run --extra docs mkdocs serve
```

Build static docs:

```bash
uv run --extra docs mkdocs build
```

Docs config and content are at:
- `mkdocs.yml`
- `docs/src/` (source)
- `docs/site/` (generated build output)

## API

- `GET /v1/price`
- `GET /v1/quote`
- `GET /v1/providers`
- `GET /v1/health`
- `GET /v1/ready`
- `GET /metrics`

When `API_KEY_AUTH_ENABLED=true`:
- all `/v1/*` endpoints require `Authorization: Bearer <api-key>`
- `/metrics` stays unauthenticated

## Request Examples

### Price

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=curve,defillama&is_vault=false'
```

### Quote

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/quote?chain_id=1&token_in=0xd533a949740bb3306d119cc777fa900ba034cd52&token_out=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&amount_in=1000000000000000000&providers=curve&include_route=false'
```

`providers` supports both styles:
- repeated: `providers=curve&providers=defillama`
- csv: `providers=curve,defillama`

## Config

Primary config file:
- `config/app.toml`

Key sections:
- `[chains]`
  - `ids = [1]`
- `[rpc]`
  - `urls = [...]` (required for `is_vault=true`)
- `[timeouts]`
  - `provider_request_timeout_ms`
  - `provider_max_retries` (default `0`)
- `[concurrency]`
  - `provider_fanout_per_request`
  - `provider_global_limit`
- `[providers]`
  - `enabled = [...]`
  - `price_priority = [...]`
  - `quote_priority = [...]`
- `[security]`
  - `api_key_auth_enabled = false`
  - `api_key_db_path = "data/api_keys.sqlite3"`
  - `api_key_rate_limit_rpm = 300`

Settings precedence:
1. environment variables
2. `.env`
3. `config/app.toml`
4. code defaults

Aggregate deadline behavior (no extra config knobs):
- price deadline = `provider_request_timeout_ms + 100ms`
- quote deadline = `provider_request_timeout_ms + 300ms`

## API Key CLI

Manage consumer API keys locally:

```bash
api-key generate
api-key list
api-key invalidate <key_id>
```

Optional machine-readable output:

```bash
api-key generate --label "ops" --json
api-key list --all --json
api-key invalidate <key_id> --reason "rotation" --json
```

## Response Contract

Price response includes:
- `token`: request token metadata
- `provider_order`: deterministic precedence used for top-level selection
- `price_data`: top-level selected price result (or `null` if all providers failed)
- `providers`: keyed map of per-provider results (includes failures)
- `summary`: aggregate stats
- `price_data.price` and `providers.*.price` are normalized USD prices
- price summary fields: `best_price`, `high_price`, `low_price`, `median_price`, `deviation_bps`
- shared summary fields: `requested_providers`, `successful_providers`, `failed_providers`

Quote response mirrors this shape using:
- `token_in`, `token_out`, `quote`, `providers`, `provider_order`, `summary`
- quote summary fields: `best_amount_out`, `best_provider`

Notes:
- Address inputs are case-insensitive.
- Response addresses are always EIP-55 checksummed.
- `chain_id` defaults to `1` (Ethereum mainnet) when omitted.
- `is_vault` defaults to `false`.
- `value_usd` has been removed.

## Provider Selection

Top-level `price` / `quote` is selected by provider precedence:
1. Request `providers` order (if provided)
2. Else configured defaults:
   - `providers.price_priority`
   - `providers.quote_priority`
3. Then any remaining enabled providers in deterministic order (alphabetical provider ID)

## Token Metadata Cache

Token metadata is cached in SQLite (`TOKEN_METADATA_DB_PATH`, default `data/token_metadata.sqlite3`).
Resolution order:
1. Existing cache entry
2. Provider response metadata
3. On-chain ERC20 metadata via multicall (if RPC is configured)
4. Logo candidates (best-effort, no request-path URL checks): provider logo, SmolDapp, yearn/tokenAssets, TrustWallet

Logo URL behavior:
- `logo_status=valid` in cache: return validated logo URL.
- `logo_status=invalid` in cache: return `logo_url=null`.
- `logo_status=unknown`: return first candidate URL (best-effort).

Force-refresh a token logo on demand:

```bash
uv run python -m token_price_agg.tools.verify_logo --chain-id 1 --token 0x22222222aEA0076fCA927a3f44dc0B4FdF9479D6
```

The command verifies candidates (`provider -> SmolDapp -> yearn/tokenAssets -> TrustWallet`) and persists:
- `valid`: stores verified `logo_url`.
- `invalid`: stores `logo_url=null` to suppress known broken links.

## Real Token Test Matrix

Configurable token/pair fixtures are in:
- `token_price_agg/tests/fixtures/ethereum_tokens.py`

This file defines:
- `MAINNET_TOKENS`
- `DEFAULT_PRICE_SYMBOLS`
- `DEFAULT_QUOTE_SYMBOLS`
- `build_directed_quote_pairs(...)`

## Observability

- `GET /v1/health` liveness
- `GET /v1/ready` readiness
- `GET /metrics` Prometheus endpoint
- `X-Request-ID` is accepted and echoed in responses

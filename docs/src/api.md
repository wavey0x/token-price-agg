# API Contract

## Authentication and Rate Limits

When `API_KEY_AUTH_ENABLED=true`, all `/v1/*` endpoints require:

- `Authorization: Bearer <api-key>`

Failure behavior:

- `401`:
  - body: `{"detail":{"code":"UNAUTHORIZED","message":"..."}}`
  - header: `WWW-Authenticate: Bearer`
- `429`:
  - body: `{"detail":{"code":"RATE_LIMITED","message":"..."}}`
  - headers: `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`

`/metrics` remains unauthenticated.

## Price

### Request

`GET /v1/price`

Query params:

- `chain_id` (required, int)
- `token` (required, EVM address)
- `providers` (optional, repeated or csv)
- `is_vault` (optional, bool, default `false`)

Example:

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=curve,defillama'
```

### Response

Key fields:

- `token`: request token metadata
- `provider_order`: precedence used for top-level selection
- `price_data`: selected successful provider result, or `null`
- `providers`: keyed object of per-provider results
- `summary`: aggregate statistics
- `price` fields in `price_data` and `providers.*` are normalized USD prices
- `summary` price fields: `best_price`, `high_price`, `low_price`, `median_price`, `deviation_bps`
- `summary` common fields: `requested_providers`, `successful_providers`, `failed_providers`

`value_usd` is removed from response.

## Quote

### Request

`GET /v1/quote`

Query params:

- `chain_id` (required, int)
- `token_in` (required, EVM address)
- `token_out` (required, EVM address)
- `amount_in` (required, positive integer string)
- `providers` (optional, repeated or csv)
- `include_route` (optional, bool, default `false`)
- `is_vault` (optional, bool, default `false`)

Example:

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/quote?chain_id=1&token_in=0xd533a949740bb3306d119cc777fa900ba034cd52&token_out=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&amount_in=1000000000000000000&providers=curve'
```

### Response

Key fields:

- `token_in`, `token_out`: request token metadata
- `provider_order`: precedence used for top-level selection
- `quote`: selected successful provider result, or `null`
- `providers`: keyed object of per-provider results
- `summary`: aggregate statistics
- `summary` quote fields: `best_amount_out`, `best_provider`
- `summary` common fields: `requested_providers`, `successful_providers`, `failed_providers`

## Provider Selection

Top-level `price`/`quote` is selected by:

1. Request `providers` order (if provided).
2. Default priority config:
   - `PRICE_PROVIDER_PRIORITY`
   - `QUOTE_PROVIDER_PRIORITY`
3. Remaining selected providers in deterministic order.

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

## Provider Status Model

Every per-provider entry includes a `status` field with exactly **4 possible values**:

| Status | Meaning | Integrator Action |
| --- | --- | --- |
| `ok` | Provider returned data successfully | Use the data |
| `no_route` | Provider cannot service this token/pair/chain (deterministic) | Don't retry this provider for this pair |
| `error` | Transient failure (timeout, upstream error, rate limit, internal) | Retry may help; check `error.code` for detail |
| `bad_request` | Invalid request (unsupported operation, provider unavailable) | Fix request parameters |

### Quick integration

```js
switch (provider.status) {
  case "ok":          // use the data
  case "no_route":    // skip this provider, don't retry
  case "error":       // retry later (check error.code if you need the reason)
  case "bad_request": // fix your request
}
```

### Error Detail

When `status` is not `ok`, the `error` object provides machine-readable detail:

```json
{
  "error": {
    "code": "TIMEOUT",
    "message": "Provider request timed out",
    "retry_after_ms": null
  }
}
```

| `error.code` | Parent `status` | Description |
| --- | --- | --- |
| `TIMEOUT` | `error` | Provider or transport timed out |
| `RATE_LIMITED` | `error` | Provider rate-limited the request. Check `retry_after_ms` if present. |
| `UPSTREAM_HTTP` | `error` | HTTP error from provider (non-200 status, connection error) |
| `UPSTREAM_PARSE` | `error` | Invalid or unparseable response from provider |
| `DEADLINE_EXCEEDED` | `error` | Provider did not respond within the aggregate deadline |
| `INTERNAL` | `error` | Internal error during provider execution |
| `INVALID_VAULT_CONVERSION` | `error` | Failed to convert vault share/asset amounts |
| `NO_ROUTE` | `no_route` | No route found or token not supported by this provider |
| `UNSUPPORTED_OPERATION` | `bad_request` | Provider does not support this operation type (e.g. quote-only provider asked for price) |
| `PROVIDER_UNAVAILABLE` | `bad_request` | Provider is disabled (e.g. missing API key) |

`retry_after_ms` is `null` unless `error.code` is `RATE_LIMITED` and the provider communicates a backoff window.

### Computed Fields

- `success`: boolean, always `true` when `status == "ok"`, `false` otherwise.
- `error`: always `null` when `status == "ok"`, always present otherwise.

## Price

### Request

`GET /v1/price`

#### Parameters

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `chain_id` | integer | no | `1` | EVM chain id. Must be `> 0`. |
| `token` | string | yes | none | Token address to price. Case-insensitive input; output is checksummed (EIP-55). |
| `providers` | list[string] | no | all available for price | Provider filter/priority for selection. Accepts repeated params and csv. Values are normalized to lowercase and deduplicated in first-seen order. |
| `use_underlying` | boolean | no | `false` | Best-effort vault handling. If token is a supported vault, service prices underlying and converts back to vault share price. If vault/web3 resolution fails, request proceeds with original token unchanged. |
| `timeout_ms` | integer | no | server default | Per-request timeout override in milliseconds. Min 50, max 30000. |

`providers` accepted formats:
- repeated: `providers=curve&providers=defillama`
- csv: `providers=curve,defillama`
- mixed: `providers=curve,defillama&providers=lifi`

`use_underlying` behavior:
- If token is a supported vault:
  - On-chain vault reads use Multicall3 when available (with per-call fallback).
  - ERC-4626 conversion uses `convertToAssets(10**decimals)` (fallback `previewRedeem`).
  - Yearn v2 conversion uses `pricePerShare()`.
  - Returned price = `underlying_price_usd * price_per_share`.
- If token is not a supported vault: request proceeds with original token unchanged.
- If RPC URLs are not configured or on-chain calls fail: request proceeds with original token unchanged.

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
- `summary` price fields: `high_price`, `low_price`, `median_price`, `deviation_bps`
- `summary` common fields: `requested_providers`, `successful_providers`, `failed_providers`
- `vault_context` is included only in top-level `price_data` (not repeated in `providers.*`)

#### Per-provider price entry

| Field | Type | Description |
| --- | --- | --- |
| `status` | string | `ok`, `no_route`, `error`, or `bad_request` |
| `success` | boolean | `true` when `status == "ok"` |
| `price` | string (decimal) \| null | USD price. `null` on failure. |
| `latency_ms` | integer | Provider response time |
| `as_of` | string (ISO 8601) \| null | When price was last updated at source. `null` if provider doesn't report this. |
| `retrieved_at` | string (ISO 8601) | When this API retrieved the value |
| `error` | object \| null | `{code, message, retry_after_ms}` on failure; `null` on success |

### Example: Price Success

```json
{
  "request_id": "f8c8fa47d3e54b9f",
  "chain_id": 1,
  "token": {
    "chain_id": 1,
    "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "symbol": "USDC",
    "decimals": 6,
    "logo_url": "https://assets.smold.app/api/token/1/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo-128.png"
  },
  "provider_order": ["defillama", "curve"],
  "price_data": {
    "provider": "defillama",
    "price": "1.0002",
    "latency_ms": 37,
    "as_of": "2026-03-05T02:30:08.000000Z",
    "retrieved_at": "2026-03-05T02:30:08.123456Z",
    "vault_context": null
  },
  "providers": {
    "defillama": {
      "status": "ok",
      "success": true,
      "price": "1.0002",
      "latency_ms": 37,
      "as_of": "2026-03-05T02:30:08.000000Z",
      "retrieved_at": "2026-03-05T02:30:08.123456Z",
      "error": null
    },
    "curve": {
      "status": "ok",
      "success": true,
      "price": "1.0001",
      "latency_ms": 52,
      "as_of": "2026-03-05T02:30:08.000000Z",
      "retrieved_at": "2026-03-05T02:30:08.138901Z",
      "error": null
    }
  },
  "summary": {
    "requested_providers": 2,
    "successful_providers": 2,
    "failed_providers": 0,
    "high_price": "1.0002",
    "low_price": "1.0001",
    "median_price": "1.00015",
    "deviation_bps": 0
  }
}
```

### Example: Price Partial Failure

```json
{
  "request_id": "016f4ce2ce95455f",
  "chain_id": 1,
  "token": {
    "chain_id": 1,
    "address": "0xD533a949740bb3306d119CC777fa900bA034cd52",
    "symbol": "CRV",
    "decimals": 18,
    "logo_url": null
  },
  "provider_order": ["curve", "defillama"],
  "price_data": {
    "provider": "curve",
    "price": "0.7421",
    "latency_ms": 91,
    "as_of": "2026-03-05T02:32:11.000000Z",
    "retrieved_at": "2026-03-05T02:32:11.450000Z",
    "vault_context": null
  },
  "providers": {
    "curve": {
      "status": "ok",
      "success": true,
      "price": "0.7421",
      "latency_ms": 91,
      "as_of": "2026-03-05T02:32:11.000000Z",
      "retrieved_at": "2026-03-05T02:32:11.450000Z",
      "error": null
    },
    "defillama": {
      "status": "error",
      "success": false,
      "price": null,
      "latency_ms": 801,
      "as_of": null,
      "retrieved_at": "2026-03-05T02:32:12.130000Z",
      "error": {
        "code": "DEADLINE_EXCEEDED",
        "message": "Provider exceeded aggregate deadline",
        "retry_after_ms": null
      }
    }
  },
  "summary": {
    "requested_providers": 2,
    "successful_providers": 1,
    "failed_providers": 1,
    "high_price": "0.7421",
    "low_price": "0.7421",
    "median_price": "0.7421",
    "deviation_bps": null
  }
}
```

### Example: Vault Price (`use_underlying=true`)

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/price?chain_id=1&token=0xe5F625e8f4D2A038AE9583Da254945285E5a77a4&use_underlying=true&providers=curve'
```

```json
{
  "price_data": {
    "provider": "curve",
    "price": "1.0834",
    "latency_ms": 74,
    "as_of": "2026-03-05T02:35:20.000000Z",
    "retrieved_at": "2026-03-05T02:35:20.224000Z",
    "vault_context": {
      "vault_type": "yearn_v2",
      "underlying_token": "0xD533a949740bb3306d119CC777fa900bA034cd52",
      "price_per_share": "1.459948592017731652",
      "block_number": 21940623
    }
  }
}
```

## Quote

### Request

`GET /v1/quote`

#### Parameters

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `chain_id` | integer | no | `1` | EVM chain id. Must be `> 0`. |
| `token_in` | string | yes | none | Input token address. Case-insensitive input; output is checksummed (EIP-55). |
| `token_out` | string | yes | none | Output token address. Case-insensitive input; output is checksummed (EIP-55). |
| `amount_in` | string (integer) | yes | none | Positive base-unit amount (for example wei). Must parse as positive integer. |
| `providers` | list[string] | no | all available for quote | Provider filter/priority for selection. Accepts repeated params and csv. Values are normalized to lowercase and deduplicated in first-seen order. |
| `include_route` | boolean | no | `false` | If `true`, provider route payload is included when provider supports it. If `false`, route is omitted (`null`) in response. |
| `use_underlying` | boolean | no | `false` | Best-effort vault handling on both legs. Supported vault legs are converted to underlying for provider quote calls, then response amounts are converted back to share units for vault output legs. If vault/web3 resolution fails, request proceeds unchanged. |
| `timeout_ms` | integer | no | server default | Per-request timeout override in milliseconds. Min 50, max 30000. |

`use_underlying` for quote:
- Applies to both `token_in` and `token_out` if either is a supported vault.
- For vault `token_in`, request `amount_in` is converted shares -> underlying assets before provider calls.
- For vault `token_out`, response `amount_out` and `amount_out_min` are converted underlying assets -> shares.
- If vault detection/web3 fails or neither token is a supported vault: request proceeds with original tokens/amounts unchanged.

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
- quote amounts are returned as strict base-unit integers (`amount_in`, `amount_out`, `amount_out_min`), not human-formatted decimals
- `summary` quote fields: `high_amount_out`, `low_amount_out`, `median_amount_out`
- `summary` common fields: `requested_providers`, `successful_providers`, `failed_providers`
- `vault_context` is included only in top-level `quote` (not repeated in `providers.*`)
- quote `vault_context` uses leg-specific share prices:
  - `price_per_share_token_in`: share price used for input-leg conversion
  - `price_per_share_token_out`: share price used for output-leg conversion
  - both fields are always present in `vault_context` and are `null` when not applicable

#### Per-provider quote entry

| Field | Type | Description |
| --- | --- | --- |
| `status` | string | `ok`, `no_route`, `error`, or `bad_request` |
| `success` | boolean | `true` when `status == "ok"` |
| `amount_in` | integer \| null | Input amount in base units. Mirrors the request value. |
| `amount_out` | integer \| null | Output amount in base units. `null` on failure. |
| `amount_out_min` | integer \| null | Minimum output for slippage. `null` if provider doesn't support or on failure. |
| `price_impact_bps` | integer \| null | Price impact in basis points. `null` if unavailable. |
| `estimated_gas` | integer \| null | Gas estimate. `null` if unavailable. |
| `latency_ms` | integer | Provider response time |
| `as_of` | string (ISO 8601) \| null | When quote was generated at source. `null` if provider doesn't report this. |
| `retrieved_at` | string (ISO 8601) | When this API retrieved the value |
| `error` | object \| null | `{code, message, retry_after_ms}` on failure; `null` on success |
| `route` | object \| null | Provider-specific route data. Only present when `include_route=true`. |

### Example: Quote Success

```json
{
  "request_id": "ed4e90a6fe4b43af",
  "chain_id": 1,
  "token_in": {
    "chain_id": 1,
    "address": "0xD533a949740bb3306d119CC777fa900bA034cd52",
    "symbol": "CRV",
    "decimals": 18,
    "logo_url": null
  },
  "token_out": {
    "chain_id": 1,
    "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "symbol": "USDC",
    "decimals": 6,
    "logo_url": null
  },
  "provider_order": ["curve"],
  "quote": {
    "provider": "curve",
    "amount_in": 1000000000000000000,
    "amount_out": 742100,
    "amount_out_min": 734679,
    "price_impact_bps": 14,
    "estimated_gas": 182000,
    "latency_ms": 89,
    "as_of": "2026-03-05T02:40:11.000000Z",
    "retrieved_at": "2026-03-05T02:40:11.330000Z",
    "route": null,
    "vault_context": null
  },
  "providers": {
    "curve": {
      "status": "ok",
      "success": true,
      "amount_in": 1000000000000000000,
      "amount_out": 742100,
      "amount_out_min": 734679,
      "price_impact_bps": 14,
      "estimated_gas": 182000,
      "latency_ms": 89,
      "as_of": "2026-03-05T02:40:11.000000Z",
      "retrieved_at": "2026-03-05T02:40:11.330000Z",
      "error": null,
      "route": null
    }
  },
  "summary": {
    "requested_providers": 1,
    "successful_providers": 1,
    "failed_providers": 0,
    "high_amount_out": 742100,
    "low_amount_out": 742100,
    "median_amount_out": 742100
  }
}
```

### Example: Quote with Multiple Providers (Mixed Success)

```json
{
  "request_id": "a1b2c3d4e5f67890",
  "chain_id": 1,
  "token_in": { "chain_id": 1, "address": "0xD533a949740bb3306d119CC777fa900bA034cd52", "symbol": "CRV", "decimals": 18, "logo_url": null },
  "token_out": { "chain_id": 1, "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "symbol": "USDC", "decimals": 6, "logo_url": null },
  "provider_order": ["curve", "odos", "lifi"],
  "quote": {
    "provider": "curve",
    "amount_in": 1000000000000000000,
    "amount_out": 742100,
    "amount_out_min": 734679,
    "price_impact_bps": 14,
    "estimated_gas": 182000,
    "latency_ms": 89,
    "as_of": null,
    "retrieved_at": "2026-03-05T02:40:11.330000Z",
    "route": null,
    "vault_context": null
  },
  "providers": {
    "curve": {
      "status": "ok",
      "success": true,
      "amount_in": 1000000000000000000,
      "amount_out": 742100,
      "amount_out_min": 734679,
      "price_impact_bps": 14,
      "estimated_gas": 182000,
      "latency_ms": 89,
      "as_of": null,
      "retrieved_at": "2026-03-05T02:40:11.330000Z",
      "error": null,
      "route": null
    },
    "odos": {
      "status": "no_route",
      "success": false,
      "amount_in": null,
      "amount_out": null,
      "amount_out_min": null,
      "price_impact_bps": null,
      "estimated_gas": null,
      "latency_ms": 120,
      "as_of": null,
      "retrieved_at": "2026-03-05T02:40:11.360000Z",
      "error": {
        "code": "NO_ROUTE",
        "message": "No route found",
        "retry_after_ms": null
      },
      "route": null
    },
    "lifi": {
      "status": "bad_request",
      "success": false,
      "amount_in": null,
      "amount_out": null,
      "amount_out_min": null,
      "price_impact_bps": null,
      "estimated_gas": null,
      "latency_ms": 0,
      "as_of": null,
      "retrieved_at": "2026-03-05T02:40:11.330000Z",
      "error": {
        "code": "PROVIDER_UNAVAILABLE",
        "message": "missing_api_key",
        "retry_after_ms": null
      },
      "route": null
    }
  },
  "summary": {
    "requested_providers": 3,
    "successful_providers": 1,
    "failed_providers": 2,
    "high_amount_out": 742100,
    "low_amount_out": 742100,
    "median_amount_out": 742100
  }
}
```

## Error Response Shape

Request/domain errors use:

```json
{
  "detail": {
    "code": "INVALID_ADDRESS",
    "message": "Invalid token address"
  }
}
```

Common request/domain error codes:
- `INVALID_ADDRESS`: malformed token address.

## Provider Selection

Top-level `price`/`quote` is selected by:

1. Request `providers` order (if provided).
2. Default priority config:
   - `PRICE_PROVIDER_PRIORITY`
   - `QUOTE_PROVIDER_PRIORITY`
3. Remaining selected providers in deterministic order.

## Nullable Fields on Success

When `status == "ok"`, a `null` value on an optional field means the provider does not supply that data. It does not indicate an error. For example:

- `as_of: null` — provider does not report a source timestamp (e.g. Odos)
- `amount_out_min: null` — provider does not compute a minimum output (e.g. Odos)
- `estimated_gas: null` — provider does not return gas estimates
- `route: null` — `include_route` was `false`, or provider has no route data

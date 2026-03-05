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

#### Parameters

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `chain_id` | integer | no | `1` | EVM chain id. Must be `> 0`. |
| `token` | string | yes | none | Token address to price. Case-insensitive input; output is checksummed (EIP-55). |
| `providers` | list[string] | no | all available for price | Provider filter/priority for selection. Accepts repeated params and csv. Values are normalized to lowercase and deduplicated in first-seen order. |
| `use_underlying` | boolean | no | `false` | If `true`, treat token as vault share when supported. The service resolves underlying token via on-chain vault methods, prices underlying, then converts back to share price using on-chain share-to-asset rate. |

`providers` accepted formats:
- repeated: `providers=curve&providers=defillama`
- csv: `providers=curve,defillama`
- mixed: `providers=curve,defillama&providers=lifi`

`use_underlying` behavior:
- If token is a supported vault:
  - ERC-4626 conversion uses `convertToAssets(10**decimals)` (fallback `previewRedeem`).
  - Yearn v2 conversion uses `pricePerShare()`.
  - Returned price = `underlying_price_usd * share_to_asset_rate`.
- If token is not a supported vault: request fails with `INVALID_VAULT`.
- If RPC URLs are not configured: request fails with `RPC_NOT_CONFIGURED`.

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

`value_usd` is removed from response.

### Example: Price Success

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/price?chain_id=1&token=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48&providers=defillama,curve'
```

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
      "error": null,
      "vault_context": null
    },
    "curve": {
      "status": "ok",
      "success": true,
      "price": "1.0001",
      "latency_ms": 52,
      "as_of": "2026-03-05T02:30:08.000000Z",
      "retrieved_at": "2026-03-05T02:30:08.138901Z",
      "error": null,
      "vault_context": null
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
      "error": null,
      "vault_context": null
    },
    "defillama": {
      "status": "timeout",
      "success": false,
      "price": null,
      "latency_ms": 801,
      "as_of": null,
      "retrieved_at": "2026-03-05T02:32:12.130000Z",
      "error": {
        "code": "DEADLINE_EXCEEDED",
        "message": "Provider request exceeded deadline"
      },
      "vault_context": null
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
      "share_to_asset_rate": "1459948592017731652/1000000000000000000",
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
| `use_underlying` | boolean | no | `false` | If `true`, vault tokens are converted to underlying before quoting. `token_in` share amounts are converted to underlying assets for quote requests. |

`use_underlying` for quote:
- Applies to both `token_in` and `token_out` if either is a supported vault.
- If neither token is a supported vault: request fails with `INVALID_VAULT`.
- If RPC URLs are not configured: request fails with `RPC_NOT_CONFIGURED`.

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

### Example: Quote Success

```bash
curl -s \
  -H "Authorization: Bearer ${API_KEY}" \
  'http://localhost:8000/v1/quote?chain_id=1&token_in=0xD533a949740bb3306d119CC777fa900bA034cd52&token_out=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48&amount_in=1000000000000000000&providers=curve'
```

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
      "route": null,
      "vault_context": null
    }
  },
  "summary": {
    "requested_providers": 1,
    "successful_providers": 1,
    "failed_providers": 0,
    "best_amount_out": 742100,
    "best_provider": "curve"
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

Example vault error when RPC is not configured:

```json
{
  "detail": {
    "code": "RPC_NOT_CONFIGURED",
    "message": "Vault resolution requires RPC_URLS"
  }
}
```

Common request/domain error codes:
- `INVALID_ADDRESS`: malformed token address.
- `INVALID_VAULT`: `use_underlying=true` but no supported vault detected.
- `RPC_NOT_CONFIGURED`: `use_underlying=true` without configured RPC URLs.

## Provider Selection

Top-level `price`/`quote` is selected by:

1. Request `providers` order (if provided).
2. Default priority config:
   - `PRICE_PROVIDER_PRIORITY`
   - `QUOTE_PROVIDER_PRIORITY`
3. Remaining selected providers in deterministic order.

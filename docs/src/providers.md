# Providers

## Supported Provider IDs

- `curve` (price + quote)
- `defillama` (price only)
- `odos` (price + quote, no API key required)
- `lifi` (price + quote, requires `LIFI_API_KEY`)
- `enso` (price + quote, requires `ENSO_API_KEY`)

## Provider Query Param

`providers` supports:

- repeated: `providers=curve&providers=defillama`
- csv: `providers=curve,defillama`
- mixed repeated + csv

## Selection vs Summary

- Top-level `price_data`/`quote` follows precedence rules (first `ok` provider in `provider_order`).
- `summary` statistics (high/low/median) are computed across all successful providers.
- `providers` always includes failed providers with `status` and `error`.

## Provider Status Values

| Status | Meaning |
| --- | --- |
| `ok` | Success |
| `no_route` | Token/pair not supported by this provider |
| `error` | Transient failure — check `error.code` for detail |
| `bad_request` | Provider can't handle this request (unsupported operation, unavailable) |

## Error Codes

When a provider fails, `error.code` gives the specific reason:

| Code | Description |
| --- | --- |
| `TIMEOUT` | Provider did not respond in time |
| `RATE_LIMITED` | Provider rate-limited the request |
| `UPSTREAM_HTTP` | HTTP error from provider |
| `UPSTREAM_PARSE` | Could not parse provider response |
| `DEADLINE_EXCEEDED` | Aggregate deadline exceeded before provider responded |
| `INTERNAL` | Internal error during provider execution |
| `INVALID_VAULT_CONVERSION` | Vault share/asset conversion failed |
| `NO_ROUTE` | No route or token not supported |
| `UNSUPPORTED_OPERATION` | Provider doesn't support this operation type |
| `PROVIDER_UNAVAILABLE` | Provider is disabled (e.g. missing API key) |

## Optional Fields per Provider

Not all providers return all optional fields. On a successful (`ok`) response, `null` means "not available from this provider":

| Field | curve | defillama | odos | lifi | enso |
| --- | --- | --- | --- | --- | --- |
| `as_of` | yes | yes | no | yes | yes |
| `amount_out_min` | yes | n/a | no | yes | yes |
| `estimated_gas` | yes | n/a | yes | yes | yes |
| `price_impact_bps` | yes | n/a | yes | yes | yes |
| `route` | yes | n/a | yes (minimal) | yes | yes |

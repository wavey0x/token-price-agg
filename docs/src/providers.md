# Providers

## Supported Provider IDs

- `curve` (price + quote)
- `defillama` (price only)
- `lifi` (price + quote, requires `LIFI_API_KEY`)
- `enso` (price + quote, requires `ENSO_API_KEY`)

## Provider Query Param

`providers` supports:

- repeated: `providers=curve&providers=defillama`
- csv: `providers=curve,defillama`
- mixed repeated + csv

## Selection vs Summary

- Top-level `price`/`quote` follows precedence rules.
- `summary.best_*` remains metric-based and may differ from top-level selection.
- `providers` always includes failed providers with `status` and `error`.

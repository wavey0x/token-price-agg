# Price API

EVM token metadata, price, and same-chain quote API with plugin-style providers.

Supported networks: Ethereum (`1`), Optimism (`10`), Gnosis (`100`), Polygon (`137`),
Arbitrum (`42161`), and Avalanche (`43114`). Pass `chain_id` on each request; omitted means
Ethereum. Quotes compare providers on that network; they do not bridge between networks.
`GET /v1/providers` advertises the enabled chain IDs for each provider. Individual tokens and
pairs may still return provider-level `no_route` results.

## Endpoints

- `GET /v1/token`
- `GET /v1/price`
- `GET /v1/quote`
- `GET /v1/providers`
- `GET /v1/health`
- `GET /v1/ready`
- `GET /metrics`

## Design Goals

- Fast and deterministic provider fan-out.
- Per-provider isolation (one provider failure does not fail whole request).
- Case-insensitive input addresses, checksummed output addresses.
- Strong observability with structured logs and Prometheus metrics.

## Quick Start

Run the API:

```bash
uvicorn price_api.app.main:app --reload
```

Run docs server:

```bash
uv run --extra docs mkdocs serve
```

Build static docs:

```bash
uv run --extra docs mkdocs build
```

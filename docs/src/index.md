# Token Price Agg

Ethereum token price and quote aggregation API with plugin-style providers.

## Endpoints

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
uvicorn token_price_agg.app.main:app --reload
```

Run docs server:

```bash
uv run --extra docs mkdocs serve
```

Build static docs:

```bash
uv run --extra docs mkdocs build
```

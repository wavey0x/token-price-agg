# AGENTS.md

Project guidance for contributors working in `/Users/wavey/yearn/token-price-api`.

## Core Goals

- Keep the service simple, fast, and highly reliable.
- Prefer clear, typed, testable code over clever abstractions.
- Optimize for stable production behavior over feature breadth.

## Architecture Rules

- Use plugin-style providers with explicit capability declaration:
  - `pricer`, `quoter`, or `both`.
- Normalize all provider outputs into shared internal models before response mapping.
- Provider failures must be isolated:
  - one provider error must not fail the whole aggregate request.
- Keep provider-specific parsing and mapping inside provider modules (or provider parsing helpers), not in route handlers.

## API Contract Invariants

- Token address input is case-insensitive.
- All token addresses returned in API responses must be EIP-55 checksummed.
- `is_vault` is optional and defaults to `false`.
- `providers` is optional and defaults to all available providers for the requested operation.
- If a provider does not support a requested token or operation, return a provider-level failure result, not a global request failure.

## Vault Handling

- `is_vault=true` means resolve vault share token to underlying value.
- Support ERC-4626 and Yearn V2 style vaults (`token()`, `pricePerShare()` style flows).
- Vault resolution should be explicit, typed, and observable (logs + metrics).

## Type Safety and Validation

- Keep strong typing across boundaries:
  - Pydantic schemas for request/response and normalized provider data.
  - Avoid untyped dict plumbing in core paths.
- Parse external provider payloads defensively:
  - tolerate schema drift
  - never throw raw parse exceptions into route handlers

## File Organization

- Keep modules focused and reasonably sized.
- Prefer splitting large files by responsibility:
  - `api/`, `core/`, `providers/`, `observability/`, `token_metadata/`, `vault/`.
- Avoid allowing any single file to grow into a monolith.

## Testing and Definition of Done

Before marking work complete, run:

1. Static checks (`ruff`, `mypy`).
2. Relevant tests for changed behavior.
3. A basic request/response smoke check against the app.

Smoke check requirement (mandatory):

- Execute at least one real API-style request/response path that validates the changed area.
- Minimum baseline:
  - import app successfully and hit `/v1/health`
  - for API-impacting changes, hit `/v1/price` or `/v1/quote` with a valid sample request
- Do not claim completion without reporting smoke-check result.

## Observability Standards

- Keep logging structured and low-noise.
- Ensure request correlation via `X-Request-ID`.
- Keep metrics low-cardinality and Prometheus-friendly.
- Add or update operational docs when behavior/telemetry contracts change.

## Current Product Decisions

- Breaking changes are acceptable at this stage (no backward compatibility requirement).
- API keys are in scope in phase 1 (`LIFI_API_KEY`, `ENSO_API_KEY`).
- Prefer practical reliability enhancements first (timeouts, retries, robust parsing), then optimization.

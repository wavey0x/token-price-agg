# Price API Resilience Improvement Plan

## Goal

Fix the production failure mode where `price-api` is alive, but provider calls cannot acquire
outbound HTTPS capacity.

The fix must address both causes:

- the shared long-lived HTTPS client can become poisoned
- bursts can create provider work before capacity is reserved

The best-practice target is:

```text
bounded work + bounded waiting + isolated provider transports + automatic recovery
```

## Incident Evidence

Observed on `electro`:

- `price-api.service` was active and running.
- `/v1/health` returned `200`.
- `journalctl` repeatedly showed provider `PoolTimeout`.
- A single-provider `/v1/price?...&providers=defillama` returned HTTP `200`, but the provider
  result failed with `INTERNAL_TRANSPORT_TIMEOUT`.
- Host-level `curl` to DefiLlama returned `200` quickly, so host networking was healthy.
- The Python process had `80` sockets in `CLOSE-WAIT`, matching `provider_global_limit = 80`.

Current code shape:

- `ProviderRegistry` creates one `HttpClient`.
- All providers share that one `HttpClient`.
- That `HttpClient` owns one long-lived `httpx.AsyncClient`.
- Provider tasks are created before the service reserves downstream capacity.

Failure model:

```text
shared HTTP pool accumulates dead/stale HTTPS sockets
-> pool/file-descriptor capacity appears occupied
-> new provider calls wait for a connection slot
-> pool acquire times out
-> provider result is INTERNAL_TRANSPORT_TIMEOUT
-> same poisoned client remains in use
```

Restarting clears the pool temporarily. It is not a design fix.

## Terminology

`price` means `GET /v1/price`.

It does not mean billing or dollars. It means "ask provider APIs for a token's USD price."

Examples:

```text
/v1/price?providers=defillama
```

Usually creates one outbound provider call.

```text
/v1/price
```

With no `providers=` filter, the service selects all available price providers. In production today
that can mean five outbound calls: `defillama`, `curve`, `odos`, `lifi`, and `enso`.

`use_underlying=true` adds vault-detection work before provider fan-out.

## Non-Negotiable Design Rules

1. No provider should depend on a single process-wide HTTPS client shared with every other provider.
2. Provider clients must be finite-lived or recyclable.
3. Capacity must be reserved before creating provider fan-out work.
4. Local waiting must be short and bounded.
5. One caller must not be able to consume all process capacity.
6. One provider must not be able to consume all provider capacity.
7. Provider-specific failure should stay a provider-level result.
8. Process-wide overload should reject quickly before creating work.
9. `/v1/ready` must reflect provider transport health; `/v1/health` can stay simple.
10. Metrics must remain low-cardinality.

## Target Request Flow

```text
request
-> auth and validation
-> select providers
-> estimate downstream cost
-> reserve global and principal capacity
-> create tasks only for provider lanes with capacity
-> run each provider through its own HTTP client
-> return normalized provider results
```

Global overload can return `503`. Per-principal overload can return `429`. Provider-lane overload
should return a provider-level failure and still run the other selected providers.

## Phase 0: Incident Recovery Only

During an active incident:

```bash
systemctl restart price-api
systemctl is-active price-api
curl -s http://127.0.0.1:18743/v1/health
curl -s "http://127.0.0.1:18743/v1/price?chain_id=1&token=0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48&providers=defillama"
```

If supported by config, temporarily set keepalive low or off:

```text
provider_max_keepalive_connections_per_provider = 0
```

This is mitigation only. The steady-state system must recover without restart.

## Phase 1: Per-Provider HTTP Clients

Replace the single shared `HttpClient` with one client per provider.

Current:

```text
one HttpClient -> all providers
```

Target:

```text
defillama -> defillama HttpClient
curve     -> curve HttpClient
odos      -> odos HttpClient
lifi      -> lifi HttpClient
enso      -> enso HttpClient
```

Each provider client needs:

- its own `httpx.AsyncClient`
- its own connection and keepalive limits
- its own transport counters
- its own recycle policy
- explicit close during app shutdown

This isolates provider pools. If one pool wedges, other providers can still work.

## Phase 2: Bounded HTTP Timeouts And Keepalive

Split the current single timeout into separate timeout budgets:

```text
pool acquire timeout: 10-25 ms
connect timeout: about 500 ms
read timeout: about 1500 ms
write timeout: about 500 ms
aggregate endpoint deadline: slightly above provider read timeout
```

Pool timeout is local capacity failure. Read/connect timeout is upstream failure. They should be
measured and handled differently.

Start with conservative keepalive:

```text
provider_max_connections_per_provider = 20
provider_max_keepalive_connections_per_provider = 2
provider_keepalive_expiry_s = 2
```

Allow `provider_max_keepalive_connections_per_provider = 0` for incident mitigation.

## Phase 3: Transport Recycling

Provider clients should be retired and replaced when unhealthy.

Recycle triggers:

- client age exceeds TTL
- client exceeds max request count
- repeated `PoolTimeout` crosses threshold
- process-level `CLOSE-WAIT` count crosses threshold

Do not depend on perfect socket-to-provider attribution. CDNs make that unreliable. Use provider
transport counters when available and process socket counts as a coarse safety signal.

Safe recycle sequence:

```text
1. create replacement client
2. route new work to replacement
3. let old in-flight calls finish until a short grace period or aggregate deadline
4. close old client
5. record recycle reason
```

Do not abandon running requests unless their deadline has expired.

## Phase 4: Admission Control And Load Shedding

Reserve capacity before creating provider tasks.

Use a weighted limiter with atomic reservation. Do not implement this as a loop that acquires one
semaphore permit at a time.

Initial cost model:

```text
price provider call = 1 provider unit
quote provider call = 1 provider unit
vault detection = 1 vault unit per token checked
```

Examples:

```text
/v1/price?providers=defillama
provider cost = 1

/v1/price with no providers filter
provider cost = selected provider count, currently up to 5

/v1/price with use_underlying=true
provider cost = selected provider count
vault cost = 1

/v1/quote with use_underlying=true
provider cost = selected quote provider count
vault cost = up to 2
```

Admission outcomes:

- global capacity exhausted: reject before fan-out with `503 Service Unavailable`
- API key or anonymous principal over capacity: reject before fan-out with `429 Too Many Requests`
- one provider lane full: return a provider-level `PROVIDER_UNAVAILABLE` result for that provider
  and continue with the remaining selected providers

Use no wait or a tiny wait, such as 10-25 ms. Do not allow an unbounded internal queue.

## Phase 5: Provider Bulkheads And Circuit Breakers

Add one bounded lane per provider:

```text
defillama max in-flight calls
curve max in-flight calls
odos max in-flight calls
lifi max in-flight calls
enso max in-flight calls
```

The global limiter protects the process. Provider bulkheads protect providers from one another.

Add circuit breakers after bulkheads are in place. Open a provider circuit for provider-specific
failures:

- repeated upstream 5xx
- repeated upstream rate limits
- repeated read/connect timeouts
- repeated provider-local pool timeouts after recycle

Do not open every provider circuit because the global admission limiter is rejecting work. That is
process overload, not proof every provider is bad.

When a circuit is open, skip that provider and return provider-level `PROVIDER_UNAVAILABLE`.

## Phase 6: Vault Resolution Hardening

`use_underlying=true` can repeatedly spend RPC work on non-vault tokens.

Add:

- positive cache for successful vault detections by `(chain_id, token_address)`
- negative cache for `not_vault` by `(chain_id, token_address)` with TTL
- info-level logging for `not_vault`
- warning/error logging for true RPC failures
- metrics for `success`, `not_vault`, `rpc_not_configured`, `rpc_timeout`, and `rpc_error`

This keeps vault resolution explicit and observable without repeating known-negative work.

## Phase 7: Observability And Readiness

Add low-cardinality metrics:

```text
price_api_admission_rejections_total{reason,operation}
price_api_admission_inflight_units{scope,operation}
price_api_provider_inflight_calls{provider,operation}
price_api_provider_pool_timeouts_total{provider,operation}
price_api_provider_transport_recycles_total{provider,reason}
price_api_provider_circuit_state{provider}
price_api_provider_circuit_transitions_total{provider,state}
price_api_process_close_wait_sockets
```

Do not label metrics with token address, request ID, raw URL, raw path, or raw exception message.

Normalize unknown HTTP paths before labeling request metrics. Scanner paths like `/.env` and
`/.git/config` should be recorded as `/unknown`.

Allowed endpoint labels:

```text
/v1/price
/v1/quote
/v1/health
/v1/ready
/v1/providers
/v1/token
/metrics
/unknown
```

Readiness behavior:

- `/v1/health`: process liveness only
- `/v1/ready`: fail only when the instance should not receive normal traffic

`/v1/ready` should fail for:

- all price-capable providers unavailable or circuit-open
- all quote-capable providers unavailable or circuit-open
- repeated provider transport pool timeouts or recycle loops
- process `CLOSE-WAIT` count above threshold

High admission rejections should appear in metrics and readiness details, but should not
automatically fail readiness. Load shedding can mean the protection is working.

## Runbook Additions

Service state:

```bash
systemctl status price-api --no-pager -l
journalctl -u price-api -n 200 --no-pager -l
```

Socket state:

```bash
pid=$(systemctl show -p MainPID --value price-api)
ss -tanp | grep "pid=$pid,"
```

Provider transport failures:

```bash
journalctl -u price-api --since "15 minutes ago" --no-pager -l \
  | grep provider_transport_failure
```

Post-implementation metrics:

```bash
curl -s http://127.0.0.1:18743/metrics \
  | grep -E 'pool_timeouts|transport_recycles|admission_rejections|close_wait|all_failed'
```

Emergency recovery:

```bash
systemctl restart price-api
```

## Suggested Starting Config

```toml
[transport]
provider_pool_timeout_ms = 25
provider_connect_timeout_ms = 500
provider_read_timeout_ms = 1500
provider_write_timeout_ms = 500
provider_max_connections_per_provider = 20
provider_max_keepalive_connections_per_provider = 2
provider_keepalive_expiry_s = 2.0
provider_client_ttl_s = 300
provider_client_max_requests = 5000
provider_recycle_pool_timeout_threshold = 10
provider_recycle_window_s = 30

[concurrency]
provider_global_units = 80
provider_per_provider_units = 20
vault_global_units = 16
admission_acquire_timeout_ms = 25

[circuit_breakers]
failure_window_s = 30
failure_threshold = 10
open_duration_s = 15
half_open_probe_count = 2
```

Tune these with production metrics. They are starting values, not guarantees.

## Implementation Order

1. Add per-provider HTTP clients and explicit shutdown cleanup.
2. Split HTTP timeout config into pool/connect/read/write.
3. Add bounded keepalive config.
4. Add client recycling on TTL, max requests, and repeated `PoolTimeout`.
5. Add global weighted admission before fan-out.
6. Add provider bulkheads and provider-level capacity failures.
7. Add circuit breakers.
8. Add vault positive and negative caches.
9. Add readiness checks for provider transport health.
10. Add low-cardinality metrics and normalize unknown HTTP paths.
11. Update runbook and production config.

Metrics should be added with the features they measure. Do not create a large metrics-only detour
before fixing transport isolation and admission control.

## Tests

Unit tests:

- selected-provider cost calculation
- atomic weighted reservation and release
- global admission rejection
- per-principal admission rejection
- provider-lane exhaustion returning provider-level failure
- client recycle trigger on repeated `PoolTimeout`
- graceful client retirement with in-flight work
- circuit breaker state transitions
- vault negative cache preventing repeated detection
- unknown-path metric normalization

Integration tests:

- one provider pool times out while other providers still succeed
- poisoned provider client is recycled and later succeeds
- global overload returns fast `503` without creating provider tasks
- per-principal overload returns fast `429`
- provider-lane overload marks only that provider as failed
- circuit-open provider is skipped without outbound HTTP
- `/v1/ready` reports not-ready when transport is wedged

Load tests:

- burst default `/v1/price`
- burst single-provider `/v1/price`
- burst `/v1/price&use_underlying=true`
- mixed `/v1/price` and `/v1/quote`

Expected load-test result:

- no unbounded pending task growth
- no sustained `CLOSE-WAIT` growth
- rejected requests fail quickly with `429` or `503`
- accepted requests keep bounded latency
- one provider failure does not break other providers
- client recycle and circuit state are visible in metrics

## Definition Of Done

The work is complete when current evidence proves:

1. Providers no longer share one process-wide HTTP client.
2. Provider clients have bounded keepalive and separate pool/connect/read/write timeouts.
3. Provider clients recycle automatically on unhealthy signals.
4. Global and per-principal capacity is reserved before provider fan-out.
5. Global/principal overload is rejected quickly without creating provider tasks.
6. Provider-lane overload becomes a provider-level failure.
7. Per-provider bulkheads prevent one provider from consuming every outbound slot.
8. Circuit breakers skip known-unhealthy providers for a bounded time.
9. Vault detection has positive and negative caching.
10. `/v1/ready` reflects provider transport health.
11. Metrics expose admission rejection, pool timeout, recycle, circuit, and `CLOSE-WAIT` signals.
12. Unknown HTTP paths are normalized in metrics.
13. The runbook documents detection, mitigation, and verification.
14. Load tests prove the service sheds load instead of accumulating unbounded pending work.

## Non-Goals

- Do not solve this only by increasing `provider_global_limit`.
- Do not rely on process restarts as the normal recovery path.
- Do not make one provider failure a global request failure.
- Do not add high-cardinality metrics.

## Summary

The core fix is:

```text
per-provider HTTP clients
+ bounded keepalive and short pool-acquire timeout
+ automatic transport recycling
+ weighted admission before fan-out
+ provider bulkheads and circuit breakers
+ vault caching
+ readiness and metrics that expose transport health
```

This sheds overload before unbounded pending work accumulates, and it isolates, detects, discards,
and replaces poisoned HTTPS pools.

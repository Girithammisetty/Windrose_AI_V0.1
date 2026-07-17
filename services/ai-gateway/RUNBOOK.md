# ai-gateway RUNBOOK

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| Data plane 503 `DEPENDENCY_UNAVAILABLE` | Redis AND Postgres ledger paths down (BR-14 fail-closed) | Restore Postgres first (source of truth); Redis counters rebuild lazily from Postgres on next window access. Never disable budget checks. |
| Latency alert + `aig_ledger_fallback` log lines | Redis down, Postgres fallback active (degraded latency, expected) | Restore Redis; fallback is automatic and reversible. |
| 503 `UPSTREAM_UNAVAILABLE` spikes on one deployment | Provider region degraded; breaker open | Check `GET /api/v1/admin/providers` (`circuit_state`, `healthy`). Drain the deployment (`POST /:id/drain`); traffic fails over by priority/cloud. |
| Tenant reports every request 402 | Governing budget exhausted (error names scope+window+reset) | `GET /api/v1/admin/spend?scope_type=…&scope_ref=…`. Raise the limit via `PATCH /api/v1/admin/budgets/:id` — takes effect immediately (limits are read per request; counters unchanged). |
| Requests silently at rung 0 with `x-windrose-degraded: budget` | Budget window ≥ degrade_pct (default 95%) | Expected FinOps behavior; raise budget or wait for window reset. |
| Cache hit rate drops to 0 for a tenant | Guardrail policy or ladder change rotated context_hash (by design) | No action; cache re-warms. Verify with `windrose.cache` span attr. |
| `budget.threshold` events duplicated | Redis SETNX guard keys lost (flush?) | Guards live 40 days (`budthr:*`); consumers must dedup by event_id anyway (MASTER-FR-032). |
| Key still works after revoke (> 30s) | `keyrev` pub/sub listener dead on a replica | Restart replica; in-process key cache TTL (30s) bounds the exposure. |
| Metering drift alert (`usage.reconciliation_drift`) | Missing usage events (outbox backlog?) or provider billing anomaly | Check outbox depth (unpublished rows), then compare per-deployment totals in the alert payload against provider invoices. |

## DLQ drain

Consumer groups (`identity.events.v1`, `usage.events.v1`) route poison
messages to `<topic>.<group>.dlq` after 5 retries (MASTER-FR-033). To drain:
fix the handler bug, then replay DLQ messages onto the source topic; handlers
are idempotent via `processed_events` dedup — replays are safe.

## Outbox

Events are written transactionally to `outbox` and published by the poller
(`OutboxDispatcher.run_once`, prod: Debezium CDC). Backlog check:
`SELECT count(*) FROM outbox WHERE published_at IS NULL` (worker GUC).

## Budget counter rebuild (Redis loss)

Redis counters are rebuilt from Postgres lazily: `budget_spend` is settled on
every request via the fallback path, and new Redis windows start from the
Postgres value on first fallback-recovery settle. For a forced rebuild, delete
`bud:*` keys — the next reserve initializes them; reservations expire in 180s.

## Rollback

Stateless service: roll back the image. Migrations are forward-only
(MASTER-FR-060); schema 0001 has no destructive follow-ups. Feature-flagged
behavior changes ship via OpenFeature (MASTER-FR-073).

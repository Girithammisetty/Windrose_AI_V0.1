# usage-service (Go)

Metering, cost-attribution and budget-enforcement authority for the Windrose
platform (BRD 17). Consumes usage events from every service, aggregates them
into per-tenant/workspace/user/agent rollups, exposes showback + chargeback
reporting, maintains budget objects whose threshold events gate LLM spend at
ai-gateway, detects spend anomalies, and reconciles metered usage against
provider bills. **Every adapter is real** — Postgres, Redpanda (Kafka), Redis,
OPA — with no runtime stubs (CONVENTIONS END STATE).

## Architecture

- **Meter store + rollups**: real Postgres 16. `usage_raw` is a monthly
  range-partitioned hypertable-style table; `usage_hourly/daily/monthly` are
  materialized rollups refreshed by the rollup engine (TimescaleDB-style done in
  plain Postgres per the deploy image, which is pgvector/pg16 not Timescale).
- **Ingestion**: real Redpanda consumer group `usage-ingest` over
  `usage.metering.v1`, `query.events.v1`, `pipeline.events.v1`,
  `ai.tool_invoked.v1`, `ai.agent_run.v1`, `ai.token_usage.v1`. A declarative
  mapping catalog (validated at startup) turns each event into raw meter
  records. Idempotent: Redis `SETNX` dedup **and** a unique constraint on
  `(tenant_id, event_id, meter_key, time)`.
- **Budgets**: evaluated on ingest and by a periodic sweep; threshold crossings
  (80/95/100) emit `budget.threshold` / `budget.exhausted` on `usage.events.v1`
  via the transactional outbox → real Kafka. This is the FinOps feedback loop
  ai-gateway consumes for admission control.
- **Authz**: real OPA sidecar over the Redis `permissions_flat` projection
  (never calls rbac synchronously). Action manifest registered with rbac at
  startup.

## Run

```
# real infra (repo root): docker compose -f deploy/docker-compose.dev.yml up -d
createdb usage   # or: psql -U windrose -c 'CREATE DATABASE usage'
make run
```

Default env wires REAL adapters (no flags):

| Var | Default | Adapter |
|---|---|---|
| `MIGRATE_DATABASE_URL` | `postgres://windrose:windrose_dev@localhost:5432/usage` | owner DSN for migrations (creates the runtime role) |
| `DATABASE_URL` | `postgres://usage_app:usage_app@localhost:5432/usage` | **non-owner** NOSUPERUSER NOBYPASSRLS runtime role (RLS applies) |
| `KAFKA_BROKERS` | `localhost:9092` | Redpanda |
| `REDIS_ADDR` | `localhost:6379` | Redis |
| `OPA_URL` | `http://localhost:8281` | OPA sidecar |
| `JWKS_URL` | identity-service JWKS | JWT verification |

RLS is enforced under the shipped **non-owner** role with `ALTER TABLE … FORCE
ROW LEVEL SECURITY`, so neither the app role nor a table owner can escape tenant
isolation.

## Test

```
make test-unit         # -short; no infra; test doubles live only in *_test.go
make test-integration  # real Postgres/Kafka/Redis/OPA; auto-skips if infra down
```

## FR traceability (implemented)

| FR | Where | Test |
|---|---|---|
| USG-FR-001/003/005 meter catalog | `internal/domain/types.go`, `store/pg.go` (SeedMeters/ListMeters) | boot seed; `GET /meters` |
| USG-FR-010/015 ingest + mapping | `internal/ingest/*` | `ingest/pipeline_test.go`, AC01 |
| USG-FR-011 idempotency | `store/raw.go` (unique constraint) + Redis dedup | AC02 |
| USG-FR-014 late events / re-rollup | `store/rollups.go` RefreshRollups (49h window) | AC08 data path |
| USG-FR-020/021/022 rollups + retention | `store/rollups.go`, `jobs/jobs.go` | AC01, AC06 |
| USG-FR-030..034 budgets + threshold events | `store/budgets.go`, `budget/window.go` | AC03, AC04, `budget/window_test.go` |
| USG-FR-032 gateway resync | `GET /budgets/:id/state`, `/budget-states` | AC04 |
| USG-FR-040/041 showback + CSV | `api/handlers_reports.go`, `store/rollups.go` | AC06 |
| USG-FR-042/043 rate cards + chargeback | `store/ratecards.go`, `store/rollups.go` | AC09 |
| USG-FR-050/051 anomaly z-score | `internal/anomaly`, `jobs/jobs.go` | AC08, `anomaly/detect_test.go` |
| USG-FR-070/071/072 reconciliation + adjustments | `internal/recon`, `store/recon.go` | AC09, `recon/variance_test.go` |
| MASTER-FR-001/003 RLS + cross-tenant 404 | `migrations/000002_rls`, `store/*` | RLS default-role test, AC10 |
| MASTER-FR-012 OPA authz | `internal/authz/opa_client.go` | OPA-sidecar test |
| MASTER-FR-034 outbox → Kafka | `store/pg.go`, `events/*` | AC03 (real Kafka) |

## Known upstream-contract note

`ai.agent_run.v1` messages currently on the dev broker carry a **string-encoded**
`payload` (non-conformant with MASTER-FR-031, which requires an object). Such
messages are correctly routed to the `ai.agent_run.v1.usage-ingest.dlq` after 5
retries (MASTER-FR-033). Real `ai.token_usage.v1` events from ai-gateway carry
object payloads and are metered end-to-end (verified in AC01/AC03). Once
agent-runtime conforms, `agent_tasks_completed` metering flows without change.

## No credential-gated exceptions

All adapters are local-protocol real. Provider-bill reconciliation reads CSV
line items (RFC 4180) from a configured object-storage prefix; the parser and
variance math are real and unit-tested. Live cloud-provider billing APIs
(AWS CUR / Azure / GCP) are the only credential-gated path, per CONVENTIONS.

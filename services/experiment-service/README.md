# experiment-service

System of record for ML experiments, runs, registered models, and **governed model
promotion**. MLflow remains the tracking backend components write to, but the platform
**never reads MLflow in the request path**: experiment-service maintains an event-driven
Postgres **mirror** (webhook ingest + reconciliation sweep) and serves every UI/agent read
locally. It owns the registered-model stage workflow (`none → staging → production →
archived`) with a **human approval gate** (four-eyes, single-production invariant),
auto-generated model cards, server-side run comparison, and an indexed metric/param/tag
query API.

Stack: Python 3.12 · FastAPI · SQLAlchemy 2 async · Alembic · Postgres (RLS) · Kafka
(Redpanda) + transactional outbox · Redis · OPA · real MLflow REST · MinIO (signed
artifacts).

## Run

```bash
make install
make migrate            # EXP_MIGRATE_URL=postgresql+psycopg://windrose:windrose_dev@localhost:5432/experiment  (privileged)
make run                # uvicorn app.main:app :8086  (REAL adapters by default)
make test-unit          # in-memory doubles, no Docker
make test-integration   # Testcontainers Postgres + live MLflow/Kafka/OPA/Redis (auto-skips if down)
make lint
```

**Privilege separation (RLS is real).** Migrations run as a **privileged** role
(`EXP_MIGRATE_URL`, superuser/owner). The running service connects as the **non-superuser,
non-owner** `experiment_app` login role (the default `EXP_DATABASE_URL`), and every tenant
table has **`FORCE ROW LEVEL SECURITY`** — so `tenant_isolation_*` policies apply to the
runtime for real (a superuser/owner DSN would silently bypass RLS). The service must never
connect as the DB owner/superuser.

`app.main` builds the **SQL + real-adapter container by default** (`EXP_USE_REAL_ADAPTERS`
defaults true): RLS-bound Postgres, real MLflow REST, real Kafka + outbox relay, real Redis
dedup, real OPA. Background workers (reconciliation sweep, promotion-expiry, inbox applier,
outbox relay) and the `pipeline.events.v1` + `dataset.events.v1` consumers run as durable
in-process tasks. The in-memory doubles are reachable only from unit tests.

## Architecture

- **Mirror ingest** — the `pipeline.events.v1` consumer creates run rows and drives status
  (`scheduled→running→finished/failed/killed`); a **webhook receiver** (`POST
  /internal/mlflow/webhook`, HMAC + delivery-id dedup) parks deliveries in `mirror_inbox`, an
  async applier upserts metrics/params/tags; a **reconciliation sweep** hits real MLflow REST
  (`runs/search`) to repair missed events and settle drift to zero.
- **Promotion gate** — `promote` creates a `pending` promotion (202); `decision` enforces
  four-eyes (approver ≠ requester → 403 `SELF_APPROVAL_FORBIDDEN`), applies the stage
  transition, and on a `production` approval **auto-archives the incumbent in the same
  transaction** (single-production invariant, backed by a partial-unique index). Agent calls
  arrive as proposals via the MCP write tools and can never auto-execute to `production`.

## Traceability (FR/AC → code → test)

| Requirement | Code | Test |
|---|---|---|
| EXP-FR-001/002 experiment CRUD + archive/restore, sync MLflow create | `domain/services.py:ExperimentService`, `adapters/mlflow_client.py` | `unit/test_experiments_api.py`, `integration/test_real_mlflow_mirror.py` |
| EXP-FR-003/004 run create + status from pipeline events | `events/consumer.py`, `RunService.create_from_pipeline/transition_status`, `domain/state.py` | `unit/test_runs_mirror.py` (AC-1) |
| EXP-FR-005/006 run mirror rows + hidden-param filtering + notes | `RunService.get_detail`, `domain/hidden.py` | `unit/test_runs_mirror.py` (BR-11) |
| EXP-FR-010/011/012 webhook ingest + inbox applier + metric/param upsert | `api/routes/internal.py`, `MirrorService`, `store/sql.py:SqlRunRepo` | `unit/test_runs_mirror.py` (AC-4), live probe |
| EXP-FR-013 reconciliation sweep (real MLflow) | `ReconciliationService`, `workers/loops.py:reconcile_loop` | `unit/test_reconciliation.py` (AC-3), `integration/test_real_mlflow_mirror.py` |
| EXP-FR-014 signed artifact URLs | `adapters/artifacts.py`, `RunService.artifact_url` | — (MinIO presign; live) |
| EXP-FR-020/021 run compare + metric history | `domain/compare.py`, `CompareService` | `unit/test_compare.py` (AC-5) |
| EXP-FR-030/031 registered models + versions + registration log + card | `RegistryService.register`, `domain/card.py` | `unit/test_registry_promotion.py` (AC-6), `test_cards.py` |
| EXP-FR-032/033 stage machine + approval gate + per-model mutex (BR-4) | `domain/state.py`, `PromotionService.promote/decide`, `store/sql.py:lock_model` | `unit/test_registry_promotion.py` (AC-7/8/10), `integration/test_promotion_gate.py`, `integration/test_concurrent_promotion.py` |
| EXP-FR-040/§6 dataset.deleted -> flag cards `training_data_unavailable` | `events/consumer.py:DatasetEventHandler`, `CardService.flag_dataset_deleted` | `unit/test_dataset_events.py` |
| BR-8/AC-13 MLflow outage: reads 200, create 503 | `ExperimentService.create`, `adapters/mlflow_client.py` | `unit/test_mlflow_down.py` |
| EXP-FR-034 agent proposals (dual attribution, no auto-production) | `mcp/facade.py`, `PromotionService` | `unit/test_registry_promotion.py` (AC-9) |
| EXP-FR-035/036 promotion history + demotion | `PromotionService.list_promotions` | `unit/test_registry_promotion.py` |
| EXP-FR-040 auto model card + editable overlay + markdown | `domain/card.py`, `CardService` | `unit/test_cards.py` (AC-14) |
| EXP-FR-050/051/052 indexed query + best + MCP tools | `QueryService`, `store/sql.py:SqlRunRepo.search/best`, `mcp/facade.py` | `unit/test_query.py` (AC-11) |
| MASTER multi-tenancy / RLS (AC-12) — FORCE RLS + non-owner runtime role | `store/sql.py:SqlUnitOfWork`, `migrations/0001`+`0002`, `config.py` (default DSN) | `unit/test_isolation_authz.py`, `integration/test_rls_isolation.py`, `integration/test_shipped_default_dsn.py` |
| AC-11 metric query index-served | `store/sql.py:SqlRunRepo.search`, `migrations/0001` `ix_run_metrics_kv` | `integration/test_query_index.py` (EXPLAIN) |
| MASTER authz (real OPA) | `api/auth.py:OpaAuthzClient` | `integration/test_opa_authz.py` |
| MASTER outbox → Kafka (AC events) | `store/sql.py:OutboxDispatcher`, `events/bus.py:KafkaEventBus` | `integration/test_kafka_outbox.py` |
| RBC-FR-022 action registration (canonical verbs; guards == manifest, no drift) | `registration.py`, route `require(...)` | `unit/test_action_manifest.py` |
| EXP-FR-032 MLflow registry stage sync on approval (+ incumbent archive) | `PromotionService._sync_mlflow_stage`, `adapters/mlflow_client.py` | `integration/test_mlflow_stage_sync.py` |

## Deviations (documented)

1. **Temporal → durable in-process workers.** The BRD names Temporal for the promotion
   14-day timer and the reconciliation cron. This build realises both as restart-safe
   in-process loops whose state lives entirely in Postgres (`promotions.expires_at`,
   `reconciliation_watermarks`, `mirror_inbox`, `outbox`). This is a real, durable
   substitution — **not a stub**: a restart re-derives all pending work from the tables. The
   promotion gate itself (guards, four-eyes, single-production, audit trail) is authoritative
   in Postgres regardless of the timer engine.
2. **MLflow webhooks.** OSS MLflow has no registry-webhook emitter, so the mirror is driven
   in production by the `pipeline.events.v1` consumer + the reconciliation sweep (both fully
   real). The webhook receiver (`/internal/mlflow/webhook`, HMAC + dedup + async applier) is
   real, exercised by tests, and ready for an MLflow build/proxy that emits webhooks.
3. **`run_metric_history` partitioning deferred** (as in dataset-service): the retention job
   enforces the 12-month hot window; native monthly partitioning is a follow-up (the
   partition key would join every unique constraint).

## Live-probe note

`app.main` was booted via `uvicorn` against the real `experiment` Postgres DB (non-privileged
`experiment_rt` role) with `EXP_USE_REAL_ADAPTERS=true`. `/readyz` reported `db: ok`; a real
flow ran end-to-end through the server: create experiment → **real MLflow experiment**; a run
logged to **real MLflow**; `POST /internal/reconcile` → `repaired_count=1`; `GET
.../runs/best` served `f1_score=0.93` from the **Postgres mirror** with the real
`mlflow_run_id` and zero MLflow calls in the read path. Real **OPA** enforced authz on every
call (denied until the workspace grant projection was present in real Redis).

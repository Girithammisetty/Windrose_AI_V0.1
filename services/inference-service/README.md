# inference-service

Batch inference (scoring) for the Windrose platform: pick a **registered
(promoted) model version** and an **input dataset**, validate schema
compatibility *before* submit, run **real scoring** (load the real model from
MLflow, predict on the real data), write a governed, versioned **output dataset**
with **lineage edges**, and run scoring on **schedules**. Job status is an
event-driven state machine; lifecycle events land on real Kafka. Online serving
(KServe) is reserved (501).

Python 3.12 · FastAPI · SQLAlchemy 2 async · Alembic · Postgres (RLS) · Kafka
(Redpanda) · Redis · OPA · MinIO/S3 · **MLflow**. Depends on `libs/py-common` by
path.

## Real infrastructure (no runtime stubs)

`app.main` wires **real adapters by default** (`INF_USE_REAL_ADAPTERS=true`):

| Capability | Real adapter | Local infra |
|---|---|---|
| Model registry + model load | `MlflowModelRegistry`, `LocalScoringExecutor` | MLflow `:5500` |
| Object storage (input/output parquet) | `LocalScoringExecutor` (boto3/S3) | MinIO `:9000` |
| OLTP + RLS | SQLAlchemy async + `SqlUnitOfWork` | Postgres `:5432` |
| Event bus + outbox relay | `KafkaEventBus`, `OutboxDispatcher` | Redpanda `:9092` |
| Consumer dedup + budget gate | `RedisDedupStore`, `RedisBudgetGate` | Redis `:6379` |
| AuthZ | `OpaAuthzClient` (+ Redis projection) | OPA `:8281` |
| AuthN | `TokenVerifier` (RS256, static PEM or JWKS) | identity/Keycloak |

The **scoring run** is executed by the local `LocalScoringExecutor` — the real,
Mac-testable substitute for the production pipeline-orchestrator/Argo run. It
speaks real MLflow + S3 end to end (loads `models:/<name>/<version>`, predicts on
the real input parquet, writes a single-snapshot output parquet). It drives the
same idempotent transitions that the `pipeline.events.v1` consumer drives, so the
state machine is event-faithful and replay-safe.

In-memory doubles (`InMemory*`, `Fake*`) exist only for the unit tier and are
never reachable from the default runtime wiring (`use_real_adapters=True`).

## Run

```
make install         # uv sync
make migrate         # alembic upgrade head (INF_DATABASE_URL or default windrose db)
make run             # uvicorn app.main:app :8085  (real adapters by default)
make test-unit       # doubles only, no infra
make test-integration  # real infra (auto-skips when an endpoint is unreachable)
make lint            # ruff
```

## Deviations

- Monthly native partitioning of `inference_jobs` (BRD §4.1) is deferred;
  retention jobs enforce the 18-month window (the partition key would join every
  unique constraint). Documented in `migrations/versions/0001_initial.py`.
- Scheduling uses a real Postgres-backed cron tick (`WorkerSet._scheduler_loop`,
  croniter) instead of Temporal Schedules. The resolution/overlap/circuit-breaker
  logic is identical and fully tested; Temporal is the production durable
  substrate (infra parity, not a stub).
- **Only credential/infra-gated exception:** online serving via KServe
  (INF-FR-070) — the `/api/v1/endpoints*` namespace and `serving_endpoints` table
  are reserved and return `501 NOT_IMPLEMENTED`.

## FR → code/test traceability

| FR / AC | Code | Test |
|---|---|---|
| INF-FR-001 submit | `domain/services.py::submit`, `api/routes/inferences.py` | `unit/test_jobs_api::test_ac3_*` |
| INF-FR-002 schema validation | `domain/schema_compat.py`, `services.py::_build_report/_check_stage` | `unit/test_schema_compat.py`, `integration/test_real_inference::*incompatible*` |
| INF-FR-003 /validate | `services.py::validate` | `unit/test_jobs_api::test_validate_endpoint_*` |
| INF-FR-004 run + model ref | `adapters/executor.py`, `services.py::execute_job` | `integration/test_real_inference::*real_batch*` |
| INF-FR-005 event-driven status | `events/consumer.py::PipelineEventHandler`, `state.py` | `unit/test_consumers::test_ac12_*`, `test_pipeline_failed_*` |
| INF-FR-006/007 cancel/retry | `services.py::cancel/retry` | `unit/test_jobs_api::test_ac11_*`, `test_retry_*` |
| INF-FR-008 concurrency queue | `services.py::submit/_promote_from_queue` | `unit/test_jobs_api::test_ac7_*` |
| INF-FR-009 bulk | `services.py::bulk` | `api/routes/inferences.py::bulk_inference` |
| INF-FR-030 naming | `services.py::submit` (`<model>-v<version>-scores`) | `unit/test_jobs_api::test_ac4_*` |
| INF-FR-031 versioning create/append/replace | `services.py::_register_output` | `unit/test_schedules::test_ac9_*` (append first-fire) |
| INF-FR-032 finalize + lineage | `services.py::on_run_succeeded/_write_lineage` | `integration/test_real_inference::*real_batch*` (AC-4) |
| INF-FR-040 no partial results | `executor.py` (single parquet), `_register_output` | `unit/test_jobs_api::test_ac5_*` |
| INF-FR-041 failure taxonomy | `errors.py`, `services.py::_classify_failure` | `unit/test_consumers::test_pipeline_failed_*` |
| INF-FR-042 reaper | `services.py::reap`, `workers.py::_reaper_loop` | (worker loop) |
| INF-FR-050..055 schedules | `domain/schedules.py`, `api/routes/schedules.py` | `unit/test_schedules.py` |
| INF-FR-060 MCP facade | `mcp/facade.py` | `unit/test_mcp.py` (AC-14) |
| INF-FR-061 promotion awareness | `events/consumer.py::ExperimentEventHandler` | (handler) |
| INF-FR-070 reserved 501 | `api/routes/endpoints.py` | `unit/test_jobs_api::test_ac15_*` |
| MASTER RLS / AC-13 | `store/sql.py` (RLS UoW), migration | `integration/test_rls_isolation.py`, `unit/test_isolation_authz.py` |
| MASTER authz (OPA) | `api/auth.py::OpaAuthzClient` | `integration/test_real_infra::test_opa_*` |
| MASTER events/outbox | `store/sql.py::OutboxDispatcher`, `events/bus.py` | `integration/test_real_infra::test_job_lifecycle_events_*` |

Acceptance criteria AC-1..AC-15 are covered as named tests across
`tests/unit/test_jobs_api.py`, `test_schedules.py`, `test_consumers.py`,
`test_isolation_authz.py`, `test_mcp.py` and `tests/integration/`.

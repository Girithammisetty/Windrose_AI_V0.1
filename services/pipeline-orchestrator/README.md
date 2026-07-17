# pipeline-orchestrator

The RETRAIN half of the Windrose learning loop: it owns the definition, validation,
compilation, and execution lifecycle of ML pipelines, and turns human triage
corrections into trained models. Consolidates V1 `pipeline-manager` + `pipeline-service`
into one service (BRD 09).

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 async · Alembic · Postgres (RLS) ·
Kafka (Redpanda) + transactional outbox · Redis · OPA · MinIO (S3) · **MLflow**.

## The critical execution decision (real on the Mac)

Pipeline templates are typed component DAGs — validated (acyclic, type-compatible edges,
arity, resource limits, terminal rules) and compiled to a **real, deterministic Argo
`WorkflowTemplate`** manifest (SHA-256 digest, idempotent). For EXECUTION the DEFAULT
backend is a **real local training executor**: given a dataset + algorithm + params it
runs genuine scikit-learn/xgboost training, logs the run + metrics + the fitted model
artifact to **real MLflow** (`:5500`, tracking + registry), and produces a registered
model version. This is not a mock — `mlflow.get_run` / the model registry show the run
and artifact afterward.

The **Argo backend** (`app/executor/argo.py`) is real code that speaks the Argo
Workflows server REST + Kubernetes watch API (informer, never polling), but is
**INFRA-GATED**: it needs a Kubernetes cluster + Argo server (no local-protocol
equivalent on the Mac), so `executor_backend` defaults to `local`. This is the single
documented exception, analogous to the cloud warehouses in `CONVENTIONS.md`.

## The learning loop (corrections → real model)

`case.disposition_applied` events (the human triage correction) are consumed from real
Kafka and assembled into a labeled training dataset: `dataset_urn + row_pk → features`,
`disposition.category → label` (`app/domain/labeling.py`). A retrain run trains a real
model on those assembled labels. Proven end-to-end in
`tests/integration/test_real_training_mlflow.py`.

## Run

```bash
make install
make migrate                     # runs as a privileged role (PPL_MIGRATE_URL)
make run                         # REAL adapters + local executor are the DEFAULT
make test-unit                   # no infra; in-memory doubles (tests set use_real_adapters=False)
make test-integration            # real infra (Postgres, MLflow, Kafka, MinIO, OPA, Redis)
make lint
```

**Real adapters are the DEFAULT** (`use_real_adapters=True`): the shipped `app.main:app`
wires the Postgres (RLS) UoW, `RedisDedupStore`, the S3 manifest store (MinIO), the
`OpaAuthzClient`, the local training executor + MLflow gateway; it registers the action
catalog with rbac, bootstraps the component/algorithm catalog into Postgres, and runs
the outbox relay to Redpanda + the Kafka consumers. The in-memory doubles are reachable
**only from tests** (which set `use_real_adapters=False`), never from the runtime.

**RLS is FORCED.** Every tenant table has `FORCE ROW LEVEL SECURITY`, and the runtime
DSN uses the non-owner, non-superuser DML role `pipeline_app` — so isolation holds even
if the runtime role owned the tables. Migrations run as a privileged role.

> macOS note: xgboost needs `libomp` (`brew install libomp`).

## FR coverage (BRD 09 §3)

| Area | FRs | Where |
|---|---|---|
| Templates & versioning | PIPE-FR-001..005 | `domain/services.py::TemplateService` |
| Validation | PIPE-FR-010..017 | `domain/dag.py`, `domain/params.py`, `domain/resources.py` |
| Compilation | PIPE-FR-020..025 | `domain/compiler.py` |
| Run lifecycle | PIPE-FR-030..038 | `domain/services.py::RunService`, `executor/local.py`, `executor/argo.py` |
| Quotas & node routing | PIPE-FR-040..042 | `RunService` quota/queue + compiler node affinity labels |
| Component & algorithm catalog | PIPE-FR-050..053 | `domain/catalog.py`, `mcp/facade.py` |
| Artifacts | PIPE-FR-060..062 | `adapters/manifest_store.py`, run `output_registered` events |

31 Must FRs implemented; PIPE-FR-005/037 (Should) implemented. Node-pool routing
(PIPE-FR-041) is emitted as manifest labels/affinity (applied by the infra-gated Argo
backend). Informer-driven status (PIPE-FR-032) is the Argo path; the local executor
drives the equivalent status transitions + events directly.

## Acceptance criteria → tests

| AC | Test |
|---|---|
| AC-1 cycle → DAG_CYCLE aliases | `unit/test_dag_validation.py::test_ac1_cycle_reports_exact_aliases` |
| AC-2 edge type mismatch | `unit/test_dag_validation.py::test_ac2_edge_type_mismatch_names_both_types`, `integration/test_dag_and_boot.py` |
| AC-3 deterministic idempotent compile | `unit/test_templates_api.py::test_ac3_compile_is_deterministic_and_idempotent`, `unit/test_compiler.py` |
| AC-5 quota queue | `unit/test_runs.py::test_ac5_quota_queue_when_concurrency_exhausted` |
| AC-6 terminate idempotent | `unit/test_runs.py::test_ac6_terminate_idempotent_single_cancel_event` |
| AC-8 xgboost tune roles | `unit/test_algorithms.py::test_ac8_*` |
| AC-9 rate limit | `unit/test_runs.py::test_ac9_rate_limit_second_run_429_with_retry_after` |
| AC-10 cross-tenant 404 / RLS | `unit/test_isolation_authz.py`, `integration/test_rls_isolation.py` |
| AC-11 resource inheritance | `unit/test_dag_validation.py::test_ac11_*` |
| AC-14 model type not runnable | `unit/test_runs.py::test_ac14_model_type_not_runnable` |
| Learning loop (corrections → real MLflow model) | `integration/test_real_training_mlflow.py` |
| Run lifecycle on real Kafka | `integration/test_kafka_lifecycle.py::test_run_lifecycle_events_on_real_kafka` |
| Labeled dataset from real disposition Kafka | `integration/test_kafka_lifecycle.py::test_labeled_dataset_from_real_disposition_kafka` |
| Real adapters + local executor by default | `integration/test_dag_and_boot.py::test_app_main_wires_real_adapters_and_local_executor` |

## Remaining stubs / documented exceptions

- **Argo Workflows backend** — real code, INFRA-GATED on a k8s cluster + Argo server
  (`executor_backend=local` is the Mac default). No `NotImplementedError`; unreachable
  infra raises `DependencyUnavailable`.
- In-memory store / dedup / feature source are unit/dev-tier doubles selected only in
  `mode="memory"` with `use_real_adapters=False` — set only by tests, never reachable
  from the shipped `app.main` default.

Verified: `make test-unit` (44) + `make test-integration` (9) green; `ruff` clean; the
shipped `app.main` wires real adapters by default and the default-DSN role
(`pipeline_app`, non-owner, FORCE RLS) proves cross-tenant isolation.

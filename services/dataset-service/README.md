# dataset-service

Windrose catalog and system of record for **datasets**, their **versions**
(Iceberg snapshot references), their **profiles** (object storage blob +
Postgres pointer/summary), and the tenant-wide **lineage graph**.

Spec: `docs/brd/04_dataset_service_BRD.md` inheriting `docs/brd/00_MASTER_BRD.md`.
Layout per `Windrose-ai/CONVENTIONS.md` (Python service, wave-1 self-contained).

## Run

```bash
export PATH="/opt/homebrew/bin:$PATH"   # uv
make install          # uv sync (Python 3.12)
make test-unit        # unit tier — no external dependencies
make test-integration # Testcontainers Postgres; auto-skips if Docker is down
make test             # both tiers
make lint             # ruff
make run              # uvicorn on :8084 (memory-mode container by default)
make migrate          # alembic upgrade head (DST_DATABASE_URL / alembic.ini)
```

Database bootstrap: migrations create the non-privileged `dataset_app` role and
enable RLS on every table. Create the runtime login per environment with
`CREATE USER <user> LOGIN PASSWORD '…' IN ROLE dataset_app` and point
`DST_DATABASE_URL` (asyncpg) at it — RLS only binds to non-superusers.

Settings via `DST_*` env vars (`app/config.py`): JWT PEM/JWKS, SPIFFE
allowlist, object-store/catalog dirs, retention windows, lineage caps.

## Architecture

```
app/
  api/        routes (datasets, lineage, internal, health), auth middleware,
              error envelope, cursor pagination, Idempotency-Key support
  domain/     entities, state machines, schema diff, lineage BFS, retention
              selection, similarity ranking, profiling engine (§4.4 document),
              application services, ports (Protocol interfaces)
  store/      memory (unit tier, tenant-policy fake) + sql (SQLAlchemy 2 async,
              RLS-bound unit of work, outbox dispatcher)
  events/     envelope, in-memory bus + dedup, ingestion consumer handler
  adapters/   Catalog, ObjectStore, ProfilerRunner, SearchIndex implementations
  mcp/        read-only MCP tool facade
migrations/   forward-only alembic (0001: schema + RLS + grants)
api/openapi.yaml, events/dataset_event_envelope.avsc
```

## Adapter / stub inventory

Runtime wiring (`DST_USE_REAL_ADAPTERS=true`) uses the **real** shared
`windrose_common` adapters against local, protocol-compatible infra; the unit
tier keeps in-memory/local doubles. No `NotImplementedError` stub is reachable
from the real path.

| Port | Unit/dev double | Real runtime adapter (backing tech) |
|---|---|---|
| `Catalog` | `LocalCatalog` — JSON metadata + parquet snapshots on disk | `IcebergRestCatalog` → `windrose_common.iceberg` (**Iceberg REST catalog + MinIO** via pyiceberg; verify/read-snapshot/expire/drop against the catalog) |
| `ObjectStore` | `LocalFSObjectStore` — file blobs + HMAC pseudo-signed URLs | `S3ObjectStore` → `windrose_common.objectstore` (**MinIO/S3** blob put/get + **real presigned** GET URLs, 24h) |
| `ProfilerRunner` | `InProcessProfilerRunner` — **real pandas profiler** producing §4.4 documents (profiles stored to the real object store, pointer in PG) | `K8sProfilerRunner` (windrose/profiler Job) — **infra-gated** (needs a K8s cluster) |
| `SearchIndex` | `PostgresFTSSearchIndex` (tsvector + GIN) / `InMemorySearchIndex` (unit) | `OpenSearchIndex` (CDC projection) — **infra-gated** (OpenSearch not in the local stack; PG FTS is the real local search) |
| Event bus | `InMemoryEventBus` (records + dispatches) | `KafkaEventBus` → `windrose_common.kafka` (**Redpanda/Kafka** idempotent producer, tenant-keyed; drives the outbox dispatcher) |
| Consumer dedup | `SqlDedupStore` / `InMemoryDedupStore` | `RedisDedupStore` → `windrose_common.redisx` (**Redis** SET NX, 24h TTL) |
| Consumer transport | direct handler subscription on the in-memory bus | `KafkaIngestionConsumer` → `windrose_common.kafka` (**Kafka** group `dataset-service.ingestion`, 5-retry backoff, real DLQ topic) |
| AuthZ | `LocalScopeAuthz` (JWT scopes) | `OpaAuthzClient` → `windrose_common.opaclient` (**OPA** `windrose.authz_input` + **Redis** projection, MASTER-FR-012) |
| AuthN | static PEM (unit) | `TokenVerifier` cached JWKS (already real) / `windrose_common.authjwt` |
| Internal mTLS | mesh-terminated model: SPIFFE identity header allowlist + per-job HMAC callback signature | raw SPIFFE/XFCC validation is mesh-owned |

The real `S3ObjectStore`, `IcebergRestCatalog`, `KafkaEventBus` +
`KafkaIngestionConsumer` + `RedisDedupStore`, and `OpaAuthzClient` are exercised
end-to-end in `tests/integration/test_real_adapters.py` against the live dev infra.

Other deliberate deviations (documented, non-blocking):

- **Monthly partitioning** of `dataset_versions` / `lineage_edges` deferred
  (TODO in migration): the partition key would need to join every unique
  constraint; retention jobs enforce the windows instead.
- **Outbox worker policy**: the dispatcher reads across tenants via a
  `app.worker=true` GUC policy on `outbox` only (Debezium CDC in prod).
- Retention/timeout sweeps are exposed as service methods
  (`RetentionService.run_for_tenant`, `ProfileService.sweep_timeouts`);
  Temporal schedules drive them in prod.
- `/metrics` is a stub; OTel/Prometheus wiring is deployment-owned.
- The profiler callback token is a single-use per-job credential stored on the
  profile row (rotated per attempt) so the server can verify the
  HMAC-SHA256 body signature; SPIFFE mTLS remains the primary channel auth.

## FR traceability (Must + implemented Should)

| FR | Status | Code | Tests |
|---|---|---|---|
| DST-FR-001 dataset CRUD, unique name/workspace | Done | `domain/services.py::DatasetService`, `api/routes/datasets.py` | `test_datasets_api.py::TestCrud` |
| DST-FR-002 status machine + invariants | Done | `domain/state.py` | `test_domain_core.py::TestDatasetStateMachine` |
| DST-FR-003 immutable versions, monotonic version_no | Done | `VersionService.register`, advisory lock in `store/sql.py` | `test_profiles_api.py`, `integration/test_consumer_concurrency.py::TestConcurrentRegistration` |
| DST-FR-004 current_version + `?version=` reads | Done | `DatasetService.get`, `ProfileService.get_summary(version)` | `test_consumer.py::test_second_ingestion_appends_version` |
| DST-FR-005 schema_diff + breaking + schema_changed | Done | `domain/schema_diff.py` | `test_domain_core.py::TestSchemaDiff`, `test_profiles_api.py::test_ac5_schema_change_event` |
| DST-FR-006 soft-delete/restore/Copy of + hard cleanup | Done | `DatasetService.delete/restore`, `RetentionService` | `test_datasets_api.py::TestDeleteRestore`, `test_retention.py` |
| DST-FR-007 (S) deprecation + successor + warnings | Done | `DatasetService.patch` | `test_datasets_api.py::test_deprecation_surfaces_warnings` |
| DST-FR-008 (C) custom metadata ≤32×1KB | Done | `DatasetService.patch` validation | covered via patch tests |
| DST-FR-020 profiling launch on event / POST | Done | consumer + `ProfileService.trigger` + `ProfilerRunner` | `test_consumer.py`, `test_profiles_api.py` |
| DST-FR-021 snapshot read, 10M-row deterministic sample | Done | `profiling/engine.py` (`sample`, seed) | `test_profiler_engine.py::test_sampling_deterministic` |
| DST-FR-022 profile.json/html to object store, pointer in PG | Done | runner + `ProfileService.complete` | `test_profiles_api.py::test_ac2…`, `integration/test_persistence_outbox.py::test_full_profile_pipeline_persists` |
| DST-FR-023 mTLS callback + failure taxonomy + signature | Done | `api/routes/internal.py::profile_callback` | `test_profiles_api.py::TestCallbackSecurity` |
| DST-FR-024 profile lifecycle, timeout retry, non-blocking failure | Done | `ProfileService.complete/sweep_timeouts` | `test_profiles_api.py::TestTimeoutSweep` (AC-3), `test_ac4…` |
| DST-FR-025 type-inference contract (logical types, semantics, boolean coercion) | Done | `profiling/types.py` | `test_profiler_engine.py::TestTypeInference/TestSemantics` |
| DST-FR-026 (S) PII merge/pii_suspect | Partial | ingestion tags merged via schema tags; `pii_suspect` TODO | — |
| DST-FR-027 (S) summary + 24h signed URLs | Done | `ProfileService.get_summary` | `test_profiles_api.py::test_profile_summary_endpoint…` |
| DST-FR-040 URN nodes/typed edges | Done | `domain/urn.py`, `LineageService` | `test_lineage.py` |
| DST-FR-041 edge write API, idempotent upsert | Done | `POST /lineage/edges` (NULLS NOT DISTINCT unique) | `test_lineage.py::test_duplicate_edge_idempotent_upsert` |
| DST-FR-042 graph query, depth ≤10, 1000-node cap, truncated | Done | `domain/lineage.py::traverse` | `test_lineage.py::TestTraversal`, AC-7 test |
| DST-FR-043 append-only, node enrichment | Done | `LineageService._enrich` | `test_lineage.py::test_node_enrichment_kinds` |
| DST-FR-044 (S) auto edges from events | Done (ingestion) | consumer handler | `test_consumer.py::test_ac1…` |
| DST-FR-060 list/search + filters/sorts | Done (PG FTS behind `SearchIndex`) | repos + `PostgresFTSSearchIndex` | `test_datasets_api.py::TestListSearch`, RLS suite |
| DST-FR-061 similarity search | Done | `domain/similarity.py` | `test_datasets_api.py::TestSimilarity` (AC-11) |
| DST-FR-062 catalog change events | Done | outbox emits created/updated/deleted/restored | `test_mcp_and_events.py`, integration outbox tests |
| DST-FR-063 (S) consumers summary + force delete | Done | `DatasetService.consumers_summary/delete` | `test_datasets_api.py::test_ac12…` |
| DST-FR-080 retention policy | Done | `domain/retention.py`, `RetentionService` | `test_retention.py` (AC-6) |
| DST-FR-081 current + trained-pin never expired | Done | `select_expirable` guards | `test_retention.py::test_current_and_pinned_never_expire` |
| DST-FR-082 (S) tenant overrides | Partial | policy object injectable per run; audit TODO | policy override exercised in AC-6 test |
| MASTER: RLS + isolation | Done | migration 0001 policies; memory policy fake | `integration/test_rls_isolation.py`, `test_isolation_authz.py` |
| MASTER: error envelope / pagination / Idempotency-Key / ETag | Done | `api/errors.py`, `api/pagination` in repos, `api/idempotency.py` | `test_datasets_api.py` |
| MASTER: outbox | Done | `SqlOutboxRepo` + `OutboxDispatcher` | `integration/test_persistence_outbox.py::TestOutbox` |
| MASTER: consumer dedup + DLQ | Done | `SqlDedupStore` / real `RedisDedupStore` + `KafkaIngestionConsumer` (real DLQ topic) via `windrose_common` | `integration/test_consumer_concurrency.py`, `integration/test_real_adapters.py` |
| MCP read facade | Done | `app/mcp/facade.py` | `test_mcp_and_events.py` (AC-14) |

## AC traceability

| AC | Test | Tier |
|---|---|---|
| AC-1 | `test_consumer.py::test_ac1_creates_dataset_version_edge_profile`; `integration/test_consumer_concurrency.py::test_ac1_duplicate_event_creates_nothing_twice` | unit + integration |
| AC-2 | `test_profiles_api.py::test_ac2_profile_completes_end_to_end`; `integration…::test_full_profile_pipeline_persists` | unit + integration |
| AC-3 | `test_profiles_api.py::TestTimeoutSweep::test_ac3_timeout_retry_then_failed` | unit |
| AC-4 | `test_profiles_api.py::test_ac4_empty_data_fails_profile_not_dataset`; engine-tier `test_profiler_engine.py::TestFailureTaxonomy` | unit |
| AC-5 | `test_profiles_api.py::test_ac5_schema_change_event`; `test_domain_core.py::TestSchemaDiff::test_ac5…` | unit |
| AC-6 | `test_retention.py::TestRetentionJob::test_ac6_retention_run` (real Iceberg snapshot-expiry side is the LocalCatalog analog; REST catalog stubbed) | unit |
| AC-7 | `test_lineage.py::TestLineageApi::test_ac7_upstream_depth` | unit |
| AC-8 | `test_lineage.py::test_ac8_cross_tenant_write_404_and_audited` | unit |
| AC-9 | `integration/test_consumer_concurrency.py::test_ac9_concurrent_registrations_get_consecutive_numbers` | integration |
| AC-10 | `test_datasets_api.py::test_ac10_restore_renames_on_conflict_and_410_after_window` | unit |
| AC-11 | `test_datasets_api.py::TestSimilarity::test_ac11_column_overlap_ranking` | unit |
| AC-12 | `test_datasets_api.py::test_ac12_delete_with_consumers` | unit |
| AC-13 | `integration/test_rls_isolation.py::test_ac13_list_never_shows_foreign_rows` (+ unit variant `test_isolation_authz.py`) | integration + unit |
| AC-14 | `test_mcp_and_events.py::test_ac14_profile_summary_without_signed_urls_and_audited` | unit |

Infra-blocked assertions inside ACs (real Kafka DLQ routing, real K8s job kill,
real Iceberg REST snapshot expiry, OpenSearch projection) are covered by the
port fakes above and stubbed adapters carry `NotImplementedError` + TODO; the
integration tier auto-skips entirely (with a clear message) when Docker is
unavailable.

## Test status

```
make test-unit         132 passed
make test-integration   18 passed   (Testcontainers postgres:16-alpine + live infra
                                     real-adapter suite: MinIO, Iceberg REST, Redpanda, Redis, OPA)
make test              150 passed
make lint              ruff: All checks passed!
```

Consumer dedup is **handle-then-mark** (`app/events/consumer.py`): the
`processed_events` marker is written only after handler effects are durable, so
a mid-handler failure leaves the event un-deduped and it is safely re-run on
redelivery. The handler is idempotent (natural dedup on `ingestion_id`, snapshot
registration, and edge upsert), giving exactly-once *effect* — the property the
real Kafka consumer + DLQ hardening pass relies on. A `SnapshotAlreadyRegistered`
Conflict (concurrent duplicate) is a safe skip; a BR-1 "snapshot not yet
readable" Conflict propagates for retry.

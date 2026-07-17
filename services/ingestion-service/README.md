# ingestion-service

Windrose ingestion-service (BRD 03): source **connections**, **ingestion jobs**
(file upload / query / scheduled / webhook), the **chunked resumable upload**
path, and the streaming pipeline into the Iceberg bronze layer. Python 3.12,
FastAPI, SQLAlchemy 2 async, alembic. Vendored wave-1 platform helpers
(JWT verify, error envelope, cursor pagination, outbox, tenant context) per
`CONVENTIONS.md`.

## Run

```bash
make install          # uv sync --group dev
make run              # uvicorn on :8083 (dev defaults: sqlite + local fakes)
make migrate          # alembic upgrade head (needs DATABASE_URL=postgresql://…)
make openapi          # regenerate api/openapi.yaml from the FastAPI app
```

Dev configuration is via env vars (`DATABASE_URL`, `WINDROSE_ENV`,
`WINDROSE_DATA_DIR`, `JWT_PUBLIC_KEY_PEM`, `JWT_ISSUER`, `JWT_AUDIENCE`) — see
`app/config.py`. With no Postgres configured the service runs on SQLite with
the local-filesystem object store and parquet-file table writer (fakes below).

## Test

```bash
make test-unit          # unit + acceptance tiers — no external dependencies
make test-integration   # Testcontainers Postgres; auto-skips if Docker is down
make test               # both tiers
make lint               # ruff check
```

The scaled 10GiB no-OOM release gate runs in the unit tier:
`tests/acceptance/test_acceptance.py::test_ac04_scaled_200mib_upload_bounded_memory`
streams a ~200MiB synthetic CSV through init→parts→complete→decode→append and
asserts peak RSS grew < 100MiB (`resource.getrusage`). The full 10GiB / 512MiB
run (ING-FR-041, NFR §9) is a CI release gate on reference hardware — TODO.

## Architecture: ports & adapters

All infrastructure sits behind interfaces in `app/domain/`. The runtime wiring
(`Settings.adapter_mode == "real"`, the default via `Settings.from_env`) uses the
**real** shared `windrose_common` adapters against local, protocol-compatible
infra (MinIO, Iceberg REST catalog, Vault, Redpanda, OPA, Redis). The unit tier
(`adapter_mode == "memory"`) wires in-memory/local doubles that `app.main` never
reaches. No `NotImplementedError` stub is reachable from the real path.

| Port (interface) | Unit-tier double | Real runtime adapter (backing tech) |
|---|---|---|
| `SecretsStore` (`domain/secrets.py`) | `InMemorySecretsStore` | `VaultSecretsStore` → `windrose_common.secrets` (**Vault KV v2** via hvac; 7-day grace destroy sweeper) |
| `ObjectStore` (`domain/objectstore.py`) | `LocalFSObjectStore` (streaming, temp+rename) | `S3ObjectStore` → `windrose_common.objectstore` (**MinIO/S3** multipart streaming, ≤1 part buffered, sha256 etag) |
| `TableWriter` (`domain/tablewriter.py`) | `ParquetFileTableWriter` (parquet + snapshot ledger) | `IcebergTableWriter` → `windrose_common.iceberg` (**Iceberg REST catalog + MinIO** via pyiceberg; two-phase stage/commit, BR-9 snapshot-summary `ingestion_id`) |
| `PolicyEngine` (`domain/policy.py`) | `StaticPolicyEngine` (deny-list double) | `OPAPolicyEngine` → `windrose_common.opaclient` (**OPA** `windrose.authz_input` + **Redis** projection) |
| `EventPublisher` (`events/outbox.py`) | `InMemoryEventPublisher` + `publish_pending` poller | `KafkaEventPublisher` → `windrose_common.kafka` (**Redpanda/Kafka** idempotent producer, tenant-keyed) |
| JWT keys (`api/auth.py`) | static RS256 public key | `JWKSKeyProvider` → `windrose_common.authjwt` (**cached JWKS refresh**, RS256, alg=none rejected) |
| `Scheduler` (`domain/scheduler.py`) | — | `InProcessScheduler` — real croniter next-fire logic in both tiers |

**Remaining credential/infra-gated adapters (the documented exception):**

| Port | Status | Why gated |
|---|---|---|
| `ConnectionProber` / `SourcePreviewer` / `QuerySource` — cloud/SaaS types (`domain/drivers/*`) | **Real SDK adapters, credential-gated** (Snowflake, Redshift, Databricks, BigQuery, Spanner, Salesforce). Registered on the runtime registries — no fake reachable. | The adapter drives the real vendor SDK/protocol, but a live pull needs real customer credentials (see **Connector matrix** below). Offline request/response shaping is contract-tested with a mocked transport (`tests/unit/test_cloud_driver_contracts.py`). |
| Object-store SOURCE connectors (`s3`, `gcs`, `azure_blob`) + `ftp` (`domain/drivers/objectsource.py`, `s3.py`, `gcs.py`, `azure_blob.py`, `ftp.py`) | **Real adapters, registered on the runtime registries** — no fake reachable. `s3` (boto3) + `ftp` (aioftp) are REAL-TESTED; `gcs`/`azure_blob` are credential-gated (real SDK). | `s3` is verified live against MinIO and `ftp` against a real in-process FTP server; `gcs`/`azure_blob` drive the real vendor SDK but a live pull needs customer credentials (see **Connector matrix** / **Going live**). Offline list/read shaping is contract-tested with an injected client (`tests/unit/test_object_source.py`). |
| `QuerySource` — `presto` (Trino) | `Fake*` double (registry default) | Trino/Presto federation is a separate wave (out of scope here). |
| `Scheduler` prod orchestrator (`TemporalScheduler`) | `NotImplementedError` | Temporal Schedules; `InProcessScheduler` carries the real cron semantics locally. Webhook buffer flush timer likewise deferred to a Temporal workflow. |

### Connector matrix (wave-2 datasource drivers)

Every driver below binds the incremental watermark as a **typed driver
parameter** — the SQL text carries only a placeholder, the value rides
out-of-band (ING-FR-061/BR-5), asserted per driver. The one protocol ceiling is
Salesforce SOQL (no bind facility): its typed `datetime` watermark is rendered
through an injection-safe canonical-literal formatter (`salesforce.py`).

| Connector | Driver | Watermark bind | Status | Test |
|---|---|---|---|---|
| postgres | asyncpg | `$1` positional | REAL-TESTED (docker) | `test_local_drivers.py` |
| mysql / mariadb | aiomysql | `%(name)s` pyformat | REAL-TESTED (docker) | `test_local_drivers.py` |
| **sqlserver** | pymssql (TDS) | `%(name)s` pyformat | **REAL-TESTED** (mssql/server:2022) | `test_new_db_drivers.py` |
| **oracle** | python-oracledb (thin async) | `:name` native | **REAL-TESTED** (gvenzl/oracle-free) | `test_new_db_drivers.py` |
| **synapse** | pymssql (TDS) | `%(name)s` pyformat | CREDENTIAL-GATED (SQL Server driver) | contract via mssql; live skip |
| sftp / http_api | asyncssh / httpx | — (file fetch) | REAL-TESTED (docker) | `test_local_drivers.py` |
| **ftp** | aioftp (FTP/FTPS) | — (file fetch) | **REAL-TESTED** (in-process pyftpdlib server) | `test_object_store_drivers.py` |
| **s3** | boto3 | typed object `LastModified` mtime (client-side, never spliced) | **REAL-TESTED** (MinIO — the local S3 API) | `test_object_store_drivers.py` |
| **gcs** | google-cloud-storage | typed object mtime (client-side) | CREDENTIAL-GATED | `test_object_source.py` (contract) + live skip |
| **azure_blob** | azure-storage-blob | typed object mtime (client-side) | CREDENTIAL-GATED | `test_object_source.py` (contract) + live skip |
| **snowflake** | snowflake-connector-python | `%(name)s` pyformat | CREDENTIAL-GATED | `test_cloud_driver_contracts.py` + live skip |
| **redshift** | redshift-connector | `%s` format | CREDENTIAL-GATED | `test_cloud_driver_contracts.py` + live skip |
| **databricks** | databricks-sql-connector | `%(name)s` pyformat | CREDENTIAL-GATED | `test_cloud_driver_contracts.py` + live skip |
| **bigquery** | google-cloud-bigquery | `@name` + `ScalarQueryParameter` | CREDENTIAL-GATED | `test_cloud_driver_contracts.py` + live skip |
| **spanner** | google-cloud-spanner | `@name` + typed `param_types` | CREDENTIAL-GATED (+ **emulator** real path) | contract + `test_cloud_drivers_live.py::…emulator…` |
| **salesforce** | httpx / REST Query API + OAuth2 | typed SOQL literal (see note) | CREDENTIAL-GATED | `test_cloud_driver_contracts.py` + live skip |

Parquet & Avro decode is memory-bounded and verified end-to-end through the full
upload→decode→append pipeline (`tests/unit/test_file_format_ingest.py`), in
addition to the streaming-decode unit tests.

### Going live: credentials per gated connector

The cloud SDKs live in an optional dependency group; install them first:

```bash
uv sync --group dev --extra cloud
```

Then provide the connector's secrets (via the API `secrets` object / Vault at
runtime) and, for the live integration tests, the env vars below:

| Connector | Config (non-secret) | Secrets / env |
|---|---|---|
| snowflake | account, username, warehouse, database, schema?, role? | `password` (or `private_key`). Live test: `SNOWFLAKE_ACCOUNT/USER/PASSWORD/WAREHOUSE/DATABASE` |
| redshift | host, port=5439, database, username | `password`. Live test: `REDSHIFT_HOST/DATABASE/USER/PASSWORD` |
| databricks | server_hostname, http_path, catalog?, schema? | `access_token`. Live test: `DATABRICKS_SERVER_HOSTNAME/HTTP_PATH/TOKEN` |
| bigquery | project_id, dataset | `credentials_json` (service-account JSON). Live test: `BIGQUERY_PROJECT_ID`, `BIGQUERY_CREDENTIALS_JSON` (+ `BIGQUERY_TEST_SQL` for the pull) |
| spanner | project_id, instance_id, database | `credentials_json`. Local real path: run the emulator (auto via testcontainers) — no creds. Live: `SPANNER_*` + creds |
| synapse | host, port=1433, database, username | `password`. Live test: `SYNAPSE_HOST/DATABASE/USER/PASSWORD` |
| salesforce | username, domain=login\|test, instance_url?, api_version | `password`, `security_token`, `client_id`, `client_secret`. Live test: `SF_USERNAME/PASSWORD/SECURITY_TOKEN/CLIENT_ID/CLIENT_SECRET` (`SF_DOMAIN` optional) |
| gcs | project_id, bucket, root_prefix=/, file_format, glob? | `credentials_json` (service-account JSON). Live test: `GCS_PROJECT_ID`, `GCS_BUCKET`, `GCS_CREDENTIALS_JSON` |
| azure_blob | account_name, container_name, root_prefix=/, file_format, glob? | `account_key` **or** `sas_token`. Live test: `AZURE_ACCOUNT_NAME`, `AZURE_CONTAINER`, `AZURE_ACCOUNT_KEY` |

**Object-store SOURCE connectors (`s3`, `gcs`, `azure_blob`).** These treat a
data-lake bucket as a source: list objects under `root_prefix`, filter by an
optional `glob` and an optional **incremental watermark** (each object's
`LastModified` mtime), then stream every matching object through the shared
format decoders into one bronze snapshot — memory-bounded, never buffering a
whole object. The incremental watermark is a **typed `datetime` compared
client-side**; it never enters any listing request (the list call carries only
Bucket+Prefix), so there is no string splicing (asserted in
`test_object_source.py::test_incremental_watermark_never_spliced_into_list_request`
and `test_object_store_drivers.py::test_s3_incremental_only_new_objects`). `s3`
uses boto3 (verified live against MinIO; targets real AWS S3 / any S3-compatible
store by omitting/setting `endpoint`); `gcs`/`azure_blob` are credential-gated.
`s3` secrets: `access_key_id` / `secret_access_key` (or an ambient role via
`role_arn` with no secret).

Execution: dev/tests run jobs inline (`Settings.inline_execution`); production
finalize is a Temporal workflow with retryable activities (ING-FR-043) — the
in-process `IngestionRunner` implements the identical step sequence and retry
semantics (5 attempts, exponential backoff + jitter). The real `IcebergTableWriter`,
`S3ObjectStore`, `VaultSecretsStore`, `KafkaEventPublisher`, `OPAPolicyEngine`
and `JWKSKeyProvider` are exercised end-to-end in
`tests/integration/test_real_adapters.py` against the live dev infra.

## FR traceability (Must + implemented Should)

| FR | Status | Code | Tests |
|---|---|---|---|
| ING-FR-001 connection CRUD, unique names | ✅ | `domain/services/connections.py` | `test_api_connections.py` |
| ING-FR-002 connector catalog (19 types: V1 parity + redshift/databricks/spanner/salesforce), typed schemas, unknown-field rejection | ✅ | `domain/connectors.py` (pydantic discriminated union) | `test_connector_configs.py` |
| ING-FR-003 secrets → Vault path only, masked reads | ✅ | `connectors.py` + `domain/secrets.py` | AC-1, `test_create_returns_envelope_and_never_secrets` |
| ING-FR-004 test-connection (saved + ad-hoc, auto-test on create), 15s timeout | ✅ real probers (postgres/mysql/mariadb/sqlserver/synapse/oracle/sftp/ftp/http_api/s3/gcs/azure_blob + cloud SDKs); bucket LIST / FTP LIST round-trips; `asyncio.wait_for` timeout enforced | `domain/drivers/*`, `connections._probe` | AC-2, `test_local_drivers.py`, `test_new_db_drivers.py`, `test_object_store_drivers.py`, `test_cloud_driver_contracts.py` |
| ING-FR-005 preview ≤100 rows, 30s timeout | ✅ real per-connector previewers (dispatch); 408 on timeout | `connections.preview`, `drivers/preview.py` | `test_preview_returns_rows_without_persisting`, `test_f3_preview_timeout_returns_408` |
| ING-FR-006 delete guard + soft delete + 7-day Vault destroy | ✅ | `connections.delete` | AC-10 |
| ING-FR-007 traffic_direction (S) | ✅ field + filter | models/routes | list filter test |
| ING-FR-008 explicit sftp/ftp types (S) | ✅ | `connectors.py` | config tests |
| ING-FR-020 job create, target XOR | ✅ | `domain/services/ingestions.py` | `test_target_xor_validation` |
| ING-FR-021 formats csv/tsv/json/jsonl/parquet/avro + error_row_limit | ✅ | `domain/decode.py` | `test_decode_formats.py`, AC-13 |
| ING-FR-022 state machine + transitions log + events | ✅ | `domain/state_machine.py`, `services/transitions.py` | full matrix in `test_state_machine.py`, `test_transitions_recorded` |
| ING-FR-023 query streaming in batches, never materialized, per-job timeout | ✅ real streaming drivers (server-side cursors / `fetchmany` / page iterators); `asyncio.wait_for` query timeout → TIMEOUT | `services/runner._attempt_query`, `domain/drivers/*` | `test_local_drivers.py`, `test_new_db_drivers.py`, `test_cloud_driver_contracts.py`, `test_f3_query_timeout_fails_job_with_timeout_category` |
| ING-FR-024 webhook mode, HMAC, 1MB cap, buffered | ✅ receive+buffer; flush→Iceberg **stub** | `services/webhooks.py` | AC-11 |
| ING-FR-025 skip_profiling in completion event | ✅ | `runner._commit_staged` payload | event payload asserted |
| ING-FR-026 progress snapshot + throttled progress events | ✅ (SSE relay is realtime-hub's) | `runner._ProgressReporter`, `GET /ingestions/{id}/progress` | AC-6 |
| ING-FR-027 cancel uncommitted (S) | ✅ | `ingestions.cancel` | `test_cancel_uncommitted_job…` |
| ING-FR-028 reingest (C) | ✅ | `ingestions.reingest` | `test_reingest_clones_terminal_job` |
| ING-FR-040 upload init/parts/complete protocol | ✅ (presigned `direct=true` TODO with cloud stores) | `services/uploads.py` | `test_chunk_assembly.py` |
| ING-FR-041 never buffer a file; RSS bound | ✅ | streaming iterators end-to-end | AC-4 (scaled RSS assert) |
| ING-FR-042 resumability, state in PG | ✅ | `uploads.get` | AC-5, integration upload test |
| ING-FR-043 complete pipeline: verify→decode→parquet→single append→notify | ✅ in-process (Temporal workflow **stub**) | `runner.py`, two-phase `TableWriter` | AC-4/12, chunk tests |
| ING-FR-044 24h expiry GC | ✅ | `uploads._ensure_open`/`gc_expired` | `test_expired_upload_returns_410…` |
| ING-FR-060 schedules cron/interval/tz/watermark/overlap | ✅ | `services/schedules.py` | `test_api_schedules.py` |
| ING-FR-061 watermark bound as driver parameter, persisted high-water | ✅ | `domain/watermark.py` | `test_watermark.py`, AC-8 |
| ING-FR-062 fires create normal jobs; skip events | ✅ | `schedules.fire` | AC-9 |
| ING-FR-063 pause/resume/run_now (S) | ✅ | routes | `test_pause_resume_and_delete` |
| ING-FR-064 file-poll schedules (S) | 🟡 **partial**: the object-store SOURCE pipeline is real — list→glob→incremental-mtime→stream-decode→one bronze snapshot (`ObjectSourceIngestor`, `s3`/`gcs`/`azure_blob`, verified live vs MinIO); wiring it into a `file_poll` schedule executor / `ingestion_mode` is still a follow-up (schedule create still rejects the template with TODO) | `domain/drivers/objectsource.py`, `schedules._validate_template` | `test_object_source.py`, `test_object_store_drivers.py`, `test_file_poll_template_is_todo` |
| ING-FR-080 error categories + error_log shape | ✅ | `domain/errors.py`, `runner._fail` | AC-13, decode tests |
| ING-FR-081 5 retries w/ backoff; POST /retry | ✅ in-process (Temporal **stub**) | `runner.execute`, `ingestions.retry` | AC-12 |
| ING-FR-082 tenant caps (5 running / 20 uploads) | ✅ | `runner._acquire_slot`, `uploads.create` | `test_tenant_concurrency_cap…` |
| ING-FR-083 PII scan (S) | ❌ **stub**: `pii_tags: []` emitted, Presidio TODO | `runner.py` | — |
| MASTER-FR-001/003/004 RLS + cross-tenant 404 + audit + isolation suite | ✅ (audit fires under RLS via SECURITY DEFINER `ing_owner_tenant`, migration `0002`) | migrations `0001`/`0002`, `store/db.py`, `services/common.py` | `test_rls_isolation.py` (incl. `test_f2_cross_tenant_denied_audit_fires_under_rls`), AC-3 |
| MASTER-FR-010/011/014 RS256 JWT, claims, no alg=none | ✅ (JWKS refresh stub) | `api/auth.py` | 401 tests |
| MASTER-FR-012/016 OPA actions | ✅ interface (OPA **stub**) | `domain/policy.py` | authz matrix test |
| MASTER-FR-020..028 envelope/pagination/idempotency/errors/trace | ✅ | `api/…` | `test_platform_contracts.py`, AC-14 |
| MASTER-FR-030..035 outbox + envelope + topic | ✅ (Kafka **stub**) | `events/outbox.py` | `test_outbox.py` (integration) |
| MASTER-FR-060..063 migrations, partitioning, indexes | ✅ (pg_partman month rotation TODO) | `migrations/versions/0001_initial.py` | `test_persistence.py` |

## AC traceability (all in `tests/acceptance/test_acceptance.py`)

| AC | Test | Notes |
|---|---|---|
| AC-1 | `test_ac01_connection_secret_only_in_vault` | |
| AC-2 | `test_ac02_unreachable_host_424_nothing_persisted` | |
| AC-3 | `test_ac03_cross_tenant_read_404_and_audited` | audit event fires under production RLS via the SECURITY DEFINER `ing_owner_tenant` probe (proven in the integration tier by `test_f2_cross_tenant_denied_audit_fires_under_rls`, which also asserts no false positive on a genuinely missing id) |
| AC-4 | `test_ac04_scaled_200mib_upload_bounded_memory` | scaled: 200MiB stream, RSS delta < 100MiB; full 10GiB/512MiB is the CI release gate |
| AC-5 | `test_ac05_resume_sends_only_missing_parts` | |
| AC-6 | `test_ac06_progress_events_monotonic` | asserts emitted `ingestion.progress` stream; SSE fan-out is realtime-hub |
| AC-7 | `test_ac07_empty_query_result_fails_decode_error` | |
| AC-8 | `test_ac08_watermark_bound_as_parameter_across_runs` | fake driver records `(sql, params)` = query log |
| AC-9 | `test_ac09_overlap_skip_no_job_and_event` | |
| AC-10 | `test_ac10_connection_delete_guard_then_vault_destroy` | |
| AC-11 | `test_ac11_webhook_hmac_and_event_id_dedup` | |
| AC-12 | `test_ac12_transient_retries_then_manual_retry_no_duplicates` | BR-9 asserted via snapshot ledger summary |
| AC-13 | `test_ac13_row_limit_exceeded_with_truncated_samples` | |
| AC-14 | `test_ac14_concurrent_same_idempotency_key_single_job` | true concurrent `asyncio.gather` |

## Known deviations / wave-2 TODOs

- `uploads.parts_confirmed` is normalized into an `upload_parts` table instead
  of a JSONB array — race-free duplicate/out-of-order part handling; the API
  shape (`parts: [{n, etag, size}]`) is unchanged.
- Webhook `path_token` is prefixed with the tenant id (`<tenant>.<random>`) so
  the RLS tenant context can be established before endpoint lookup.
- **Now real** (via `windrose_common`, tested against live infra): MinIO/S3
  object store, Iceberg REST-catalog table writer, Vault KV v2 secrets,
  Redpanda/Kafka event publisher, OPA policy engine, cached JWKS refresh.
- **Now real** (datasource drivers — see the **Connector matrix** above):
  Postgres/MySQL/MariaDB/SQL Server/Synapse/Oracle/SFTP/FTP/HTTP verified against
  docker (FTP against an in-process pyftpdlib server); the `s3` object-store
  source verified live against MinIO; Snowflake/Redshift/Databricks/BigQuery/
  Spanner/Salesforce and the `gcs`/`azure_blob` object stores are real SDK
  adapters, contract-tested with mocked transports/injected clients and
  credential-gated for a live run (Spanner also runs against its emulator).
- Still deferred: direct-to-storage presigned part URLs (`direct=true`),
  Temporal workflows / `TemporalScheduler`, **`presto`/Trino federation** and an
  **`iceberg` source type** (pyiceberg scan of an existing table), wiring the
  object-store source into a runner **file-poll schedule** (ING-FR-064: the
  `ObjectSourceIngestor` pipeline is real and reusable, but no new
  `ingestion_mode`/schedule executor is added yet — like SFTP/HTTP, the fetchers
  are proven at driver level), PII scan, webhook buffer flush→Iceberg, monthly
  partition rotation (pg_partman), Helm chart / RUNBOOK.
- Redis-based consumer dedup / webhook `event_id` dedup: the shared
  `windrose_common.redisx.RedisDedupStore` (24h TTL) is available; the DB-table
  path remains for the wave-1 webhook dedup (same 24h semantics).
- `/metrics` is a minimal Prometheus counter exposition; full OTel/GenAI
  instrumentation is wave-2.

## Contract artifacts

- `api/openapi.yaml` — generated from the app (`make openapi`), 25 paths.
- `events/ingestion_event_envelope.avsc` — MASTER-FR-031 envelope for
  `ingestion.events.v1`.

# query-service

Windrose's single SQL execution broker (BRD 05): saved queries with **typed
variables and safe substitution** (bound parameters only — the V1
`process_vars!` string-splicing/first-variable-only defect is designed out),
**AST-based read-only statement enforcement** (replaces V1's bypassable
regex), **engine routing** (real in-process DuckDB + Trino/warehouse stub
adapters), **dry-run/cost ceilings**, **chunked result streaming with
paginated JSON at the edge**, per-tenant **concurrency governance**,
**query history with PII redaction**, cancellation, result cache and
retention GC. Postgres metadata under row-level security; transactional
outbox for events.

## Run

```bash
# unit tier — no external dependencies (in-process DuckDB included)
make test-unit

# integration tier — requires Docker (Testcontainers Postgres + real DuckDB)
make test-integration    # auto-skips with a clear message when Docker is down

# everything
make test
make vet && make lint

# run the server (applies migrations on boot; defaults to the local
# compose infra: Redpanda :9092, Redis :6379, OPA :8281)
DATABASE_URL=postgres://postgres:postgres@localhost:5432/query?sslmode=disable \
DATASET_SERVICE_URL=http://dataset-service \
make run
```

Runtime adapters are **real by default**: events publish to Redpanda via the
shared go-common Kafka producer, and authorization goes through the OPA sidecar
over the Redis `permissions_flat` projection. `KAFKA_BROKERS=false` is the only
escape hatch — it swaps in the in-memory publisher for broker-less local dev.
There is no allow-all authz escape hatch in the runtime path.

Key environment: `DATABASE_URL`, `LISTEN_ADDR` (`:8080`), `RESULTS_ROOT`
(result parts + exports), `DATASET_SERVICE_URL`, `DUCKDB_PATH` /
`DUCKDB_READONLY`, `TRINO_ENDPOINT`, `WAREHOUSE_ENABLED`,
`KAFKA_BROKERS` (default `localhost:9092`; `false` = in-memory dev publisher),
`SCHEMA_REGISTRY_URL`, `OPA_URL` (default `http://localhost:8281`),
`REDIS_ADDR` (default `localhost:6379`), `JWKS_URL` / `JWT_ISSUER` /
`JWT_AUDIENCE`, `EXPORT_SIGNING_SECRET`. Health: `/healthz`, `/readyz`,
`/metrics`.

## Engine / adapter inventory

| Engine | Status | Notes |
|---|---|---|
| `duckdb` | **Real** (`internal/engine/duckdb.go`, marcboeker/go-duckdb) | In-process; fresh single-connection worker per execution, per-worker `memory_limit` (2GB), recycled after each query (BR-7); optional `access_mode=read_only`; prepared `$n` statements; context cancellation kills the statement. |
| `trino` | **Compiling stub** (`internal/engine/stubs.go`) | Health + routing contracts real; `Execute` returns `NOT_IMPLEMENTED` with a TODO covering `EXECUTE … USING` bound params, kill via `DELETE /v1/query/{id}`, scan-byte stats. |
| `warehouse` | **Compiling stub** | Athena/BigQuery/Synapse per cell cloud; same port, TODO documents per-cloud bound-parameter and cancel mappings. |

Routing (§4.3, `internal/engine/router.go`): tenant `warehouse_primary` →
warehouse; est. scan ≤ 500MB ∧ datasets ≤ 5GB ∧ portable → duckdb; else
trino; trino down → warehouse with `ENGINE_FALLBACK` warning; hints may
promote but never force duckdb above thresholds (`HINT_OVERRIDDEN`).

## Safety design (what the tests prove)

- **No string splicing, ever**: `:name` placeholders are rewritten to `$n`
  by a tokenizer that respects strings/comments/casts/dollar-quotes; values
  travel only in the driver argument list. Every occurrence of every
  variable binds (the V1 first-variable-only bug is structurally
  impossible). Lists expand to placeholder sets; dataset refs substitute
  resolver-provided engine-quoted identifiers only (BR-1). Injection
  corpora are executed against real DuckDB and asserted inert (AC-2).
- **AST classification** (pg_query, `internal/sqlsafe/classify.go`): single
  statement, allow-listed `SelectStmt` only, whole-tree walk — CTE-wrapped
  DELETE/INSERT/UPDATE, multi-statement batches, comment/case obfuscation,
  `SELECT … INTO`, `FOR UPDATE`, `EXPLAIN [ANALYZE]`, `SET`, `CALL`, `COPY`
  all rejected with 403 `STATEMENT_NOT_ALLOWED`. Fails closed on unknown
  statement kinds.
- **Tenant namespace guard** (BR-2): every resolved table must live in the
  tenant's namespaces; system catalogs blocked except a whitelisted
  `information_schema` subset; CTE names exempt.

## Deviations / stubs (all honestly labelled in code)

- **Result parts are JSONL chunks, not Arrow IPC** (`internal/results`):
  the BRD's streaming semantics — bounded write buffer (≤4MB flushes, far
  under the 64MB cap), sealed parts, stable cursors, engine-decoupled reads
  (BR-14), 24h GC — are implemented; swapping the part codec for Arrow is
  localized to that package. `Accept: application/vnd.apache.arrow.stream`
  returns 501 until then.
- **Parquet export** → 501 stub; CSV export with HMAC-signed 24h URLs works.
- **Kafka publisher is real** (`internal/events/gocommon.go`): the outbox
  relay drains committed rows to Redpanda through the shared go-common
  producer (idempotent, tenant-keyed, optional Schema Registry). The
  in-memory publisher is now a unit-test double only, selectable at runtime
  solely via the `KAFKA_BROKERS=false` broker-less-dev escape hatch. Proven
  end-to-end by `TestRealKafkaPublishAndConsume` (publish → real Redpanda →
  consume via the shared consumer group).
- **Authorization is real OPA** (`internal/authz/opa_client.go`): the shared
  go-common `opaclient` loads the caller's `permissions_flat` projection from
  Redis and evaluates the OPA sidecar's `windrose.authz_input` bundle. The
  allow-all/static authorizers are unit-test doubles only (no runtime escape
  hatch). Proven by `TestRealOPAAuthorizationDecision` (real Redis projection
  + real OPA container, direct and through the `RequireAction` middleware).
- **Concurrency slots are in-process** (single-replica semantics); the
  admission protocol lives behind `exec.SlotManager` for the Redis move.
- **Estimator** sums dataset-service size stats (confidence high/low);
  EXPLAIN-based per-engine estimators plug in via `exec.EstimateFn`.
- **MCP read tools** are registered in tool-registry (BRD 13) against the
  REST endpoints here; no separate MCP server ships in this service.
- Realtime-hub status streaming not wired; status via `GET /executions/{id}`.
- 2M-row/64MB-RSS soak (AC-9 full size) is a release-gate perf test; CI
  exercises the identical code path at 120k rows / 12+ sealed parts.

## FR traceability

| FR | Status | Code | Tests |
|---|---|---|---|
| QRY-FR-001 saved-query CRUD + versions + module rule | ✅ | `api/handlers_queries.go`, `store/{pg,mem}.go` | `TestSavedQueryCRUD`, `TestSavedQueryVersionPinning_PG` |
| QRY-FR-002 typed declarations, `:name` only, `{var}` rejected | ✅ | `domain/variables.go`, `sqlsafe/scan.go` | `TestScanRejectsLegacyVarSyntax`, `TestValidateDecls`, `TestSaveTimeValidation` |
| QRY-FR-003 safe substitution, bound params only | ✅ | `sqlsafe/rewrite.go`, `engine/duckdb.go` | `TestRewriteNeverSplicesValues`, `TestRewriteFuzzCorpusValuesInert`, `TestDuckDBInjectionPayloadsInert`, `TestBrokerParameterizedExecution` |
| QRY-FR-004 all required supplied; unknown → 422; undeclared placeholder → save-time 422 | ✅ | `domain/variables.go`, `api/handlers_queries.go` | `TestBindValuesMissingAndUnknownTogether`, `TestRunVariableProblems`, `TestSaveTimeValidation` |
| QRY-FR-005 dataset refs resolved; unresolved → 422 | ✅ | `datasets/resolver.go`, `exec/plan.go` | `TestScanDatasetRefs`, `TestBrokerDeletedDataset`, `TestSaveTimeValidation` |
| QRY-FR-006 ad-hoc `/sql/run` (S) | ✅ | `api/handlers_sql.go` | `TestAdhocRunAndDryRun` |
| QRY-FR-020 AST classification, single SELECT, 403 | ✅ | `sqlsafe/classify.go` | `TestClassifyRejectsWriteStatements` (regex-bypass corpus), `TestAC3_StatementClassification` |
| QRY-FR-021 identifier-level tenant guard | ✅ | `sqlsafe/guard.go` | `TestGuardRejectsForeignNamespaces`, `TestGuardAllowsInfoSchemaSubset` |
| QRY-FR-022 agent forced dry-run + LIMIT injection + stricter ceilings | ✅ | `exec/plan.go` | `TestBrokerAgentHardening`, `TestAC6_AgentGuardrails` |
| QRY-FR-040 engines + routing decision recorded | ✅ (trino/warehouse stubs) | `engine/{engine,router,duckdb,stubs}.go` | `TestRouteDecisionTable`, `TestBrokerRoutesLargeToTrino`, `TestAC5_RoutingAndCeilingRecorded` |
| QRY-FR-041 dry-run + estimates | ✅ | `exec/{plan,broker}.go`, `api/handlers_sql.go` | `TestBrokerDryRun`, `TestAdhocRunAndDryRun` |
| QRY-FR-042 enforced ceilings (plan + runtime) | ✅ | `domain/limits.go`, `exec/broker.go` | `TestEffectiveCeilings`, `TestBrokerPlanTimeCeiling`, `TestBrokerRuntimeCeilingKill`, `TestBrokerResultRowsCeiling` |
| QRY-FR-043 sync/async modes, USE_ASYNC | ✅ | `exec/broker.go` | `TestBrokerSyncMode`, `TestSyncModeAPI` |
| QRY-FR-044 per-tenant caps, FIFO queue, fairness, 429 | ✅ | `exec/slots.go` | `TestSlotsCapQueueAndOverflow`, `TestSlotsPerUserFairness`, `TestSlotsAgentSubCap`, `TestBrokerQueueOverflow429`, `TestAC7_ConcurrencyCapQueue` |
| QRY-FR-045 cancellation ≤5s, partial accounting | ✅ | `exec/broker.go` | `TestBrokerCancelRunning`, `TestAC11_CancelRunning` |
| QRY-FR-046 result cache (S) | ✅ | `exec/broker.go` (cache key pins dataset versions) | `TestBrokerResultCache`, `TestAC10_ResultCache` |
| QRY-FR-060 chunked streaming, bounded memory | ✅ (JSONL parts; Arrow deviation above) | `results/store.go` | `TestStoreChunkedPagination`, `TestAC9_StreamingPagedResults` |
| QRY-FR-061 paginated JSON edge, limit ≤ 10000 | ✅ | `api/handlers_executions.go` | `TestRunSavedQueryEndToEnd`, `TestAC9` |
| QRY-FR-062 retention 24h + export signed URL | ✅ (CSV; parquet 501) | `results/store.go`, `api/handlers_executions.go` | `TestStoreGoneAfterGC`, `TestExportAndDownload`, `TestAC13_ResultRetention` |
| QRY-FR-063 uniform JSON type mapping | ✅ | `results/json.go` | `TestMapValue` (NaN→null+warning, int64>2^53→string, decimal string, date/timestamp, base64, nested) |
| QRY-FR-080 history rows for every execution incl. dry-runs/failures | ✅ | `store/{pg,mem}.go`, `exec/broker.go` | `TestBrokerPlanTimeCeiling`, `TestBrokerDryRun`, `TestListExecutionsPagination` |
| QRY-FR-081 stats endpoint (S) | ✅ | `api/handlers_stats.go` | `TestStatsEndpoint` |
| MASTER-FR-001..004 RLS + isolation + cross-tenant audit | ✅ | `migrations/000002_rls.up.sql`, `store/pg.go` | `TestIsolationSuiteUnit` (in-memory fake), `TestAC12_IsolationSuiteRLS` (NOSUPERUSER role) |
| MASTER-FR-010/011/014 JWT RS256, alg=none forbidden | ✅ | `api/jwt.go` | `TestAuthentication` |
| MASTER-FR-012/016 OPA port + action names, denial audit | ✅ (real OPA sidecar) | `authz/opa_client.go`, `api/middleware.go` | `TestAuthzMatrix` (unit fake), `TestRealOPAAuthorizationDecision` (real OPA + Redis) |
| MASTER-FR-020..028 envelope, uuidv7, cursor pagination, errors, idempotency, trace | ✅ | `api/respond.go`, `api/middleware.go` | `TestErrorEnvelopeAndTraceID`, `TestIdempotencyReplay`, `TestListExecutionsPagination` |
| MASTER-FR-030..035 events, outbox, envelope | ✅ (real Redpanda) | `events/*` (`gocommon.go`), `store/pg.go` | `TestBrokerOutboxRelay`, `TestOutboxRelay_PG`, `TestRealKafkaPublishAndConsume` |
| MASTER-FR-040..042 audit incl. agent dual attribution | ✅ | `api/middleware.go`, `events/events.go` | `TestAgentOBOAttribution` |
| MASTER-FR-060..063 migrations, partitioned executions, indexes | ✅ | `migrations/000001_init.up.sql` | applied in integration TestMain |

## AC traceability

| AC | Test(s) |
|---|---|
| AC-1 both variables bound, placeholders at the engine | `TestRewrite_process_vars_multi_variable`, `TestDuckDB_process_vars_multi_variable`, `TestBrokerParameterizedExecution`, `TestAC1_ProcessVarsMultiVariable_EndToEnd` |
| AC-2 injection value inert, fuzz corpus | `TestDuckDBInjectionPayloadsInert`, `TestRewriteFuzzCorpusValuesInert`, `TestAC2_InjectionValueInert` |
| AC-3 AST rejection incl. multi-statement/obfuscation/CTE-DML | `TestClassifyRejectsWriteStatements`, `TestSaveTimeValidation`, `TestAC3_StatementClassification` |
| AC-4 missing + undeclared listed together | `TestBindValuesMissingAndUnknownTogether`, `TestRunVariableProblems` |
| AC-5 routing legs + plan-time 422, reasons in history | `TestRouteDecisionTable`, `TestBrokerPlanTimeCeiling`, `TestBrokerRoutesLargeToTrino`, `TestAC5_RoutingAndCeilingRecorded` |
| AC-6 agent LIMIT injection + dry-run + 5GB ceiling | `TestBrokerAgentHardening`, `TestAC6_AgentGuardrails` |
| AC-7 cap 10, queue_position, FIFO start, 61st → 429 | `TestSlotsCapQueueAndOverflow`, `TestBrokerQueueOverflow429`, `TestAC7_ConcurrencyCapQueue` |
| AC-8 runtime kill ≤5s + ceiling event | `TestBrokerRuntimeCeilingKill`, `TestAC8_RuntimeCeilingKill` |
| AC-9 chunked pages, stable cursors (CI-sized; full soak = release gate) | `TestStoreChunkedPagination`, `TestAC9_StreamingPagedResults` |
| AC-10 cache hit / version-bump miss / bypass | `TestBrokerResultCache`, `TestAC10_ResultCache` |
| AC-11 cancel running, partial scan bytes | `TestBrokerCancelRunning`, `TestAC11_CancelRunning` (engine-specific Trino kill in the adapter TODO) |
| AC-12 isolation suite, 404 + audit per endpoint | `TestIsolationSuiteUnit` (unit fake), `TestAC12_IsolationSuiteRLS` (Postgres RLS) |
| AC-13 410 after retention, history persists | `TestResultsRetention`, `TestAC13_ResultRetention` |
| AC-14 PII param redacted, non-PII clear | `TestBrokerPIIRedaction`, `TestAC14_PIIRedactionInHistory` |

## Layout

```
cmd/server/            wiring + config
internal/api/          chi router, JWT, envelope, idempotency, handlers
internal/authz/        OPA sidecar port + fakes
internal/datasets/     dataset-service resolver (HTTP + static fake)
internal/domain/       types, typed variables, ceilings, state machine, errors
internal/engine/       Engine port, DuckDB (real), Trino/warehouse stubs, router
internal/events/       envelope, publisher, outbox relay, inbound consumers
internal/exec/         broker: plan → admit → run → finish; slots; metrics
internal/results/      chunked part store, JSON edge mapping, export, GC
internal/sqlsafe/      scanner, rewriter, AST classifier, tenant guard
internal/store/        Store port: Postgres (RLS) + in-memory fake
migrations/            forward-only SQL (init + RLS)
test/integration/      Testcontainers PG + real DuckDB, AC suite
```

Retention jobs: results GC every 15 min (24h TTL); `executions` is
month-partitioned with a 13-month retention policy (partition detach →
Iceberg archive) executed by the platform retention job.

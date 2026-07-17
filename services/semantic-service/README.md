# semantic-service

Windrose's governed **semantic layer**: per-workspace semantic models
(entities bound to dataset URNs, dimensions, measures, join paths), **verified
NL↔SQL queries** with an approval workflow, and the core product — the
**compile API**: `(metrics + dimensions + filters + time grain) → safe,
parameterized SQL`, executed by query-service. One definition, two consumers:
chart-service renders every aggregating chart through compile, and the
analytics agent answers through the same compiler via MCP read tools — a
metric can never mean two different things in a chart and a chat answer
(SEM-FR-081, enforced by a byte-identity contract test).

Spec: `docs/brd/06_semantic_service_BRD.md` inheriting `docs/brd/00_MASTER_BRD.md`.
Layout per `Windrose-ai/CONVENTIONS.md` (Python service, wave-1 self-contained).


## Run

```bash
export PATH="/opt/homebrew/bin:$PATH"   # uv
make install          # uv sync (Python 3.12)
make test-unit        # unit tier — no external dependencies (incl. in-process DuckDB execution tests)
make test-integration # Testcontainers Postgres (pgvector/pgvector:pg16); auto-skips if Docker is down
make test             # both tiers
make lint             # ruff
make run              # uvicorn on :8086 (memory-mode container by default)
make migrate          # alembic upgrade head (SEM_DATABASE_URL / alembic.ini)
```

Database bootstrap: migrations require the **pgvector** extension
(`CREATE EXTENSION vector`, needs superuser — CI/integration use the
`pgvector/pgvector:pg16` image), create the non-privileged `semantic_app` role
and enable RLS on every table. Create the runtime login per environment with
`CREATE USER <user> LOGIN PASSWORD '…' IN ROLE semantic_app` and point
`SEM_DATABASE_URL` (asyncpg) at it — RLS only binds to non-superusers.

Settings via `SEM_*` env vars (`app/config.py`): JWT PEM/JWKS, SPIFFE
allowlist, compile caps, definition size limits, reporting timezone,
embedding dimension.

## Architecture

```
app/
  api/        routes (models/versions/workflow, compile, verified-queries,
              tools, health), auth middleware, error envelope, idempotency
  domain/     definition schema+validation+diff, restricted expression grammar
              (parser → AST), state machines, SQL read-only guard, bootstrap
              deriver, application services, ports
  compiler/   THE compiler: dialect table, request normalization (regex gates),
              join resolution, deterministic SQL assembly, relative time ranges,
              chart-config → compile-request mapping
  store/      memory (unit tier, tenant-policy fake) + sql (SQLAlchemy 2 async,
              RLS-bound unit of work, projections rebuild, outbox dispatcher,
              pgvector ANN search)
  events/     envelope, in-memory bus + dedup, consumers (dataset schema
              changes → health, chart reverse index, workspace deletion)
  adapters/   DatasetClient, QueryServiceClient (dry-run), Embeddings
  mcp/        MCP-facing tool facade + JSON Schemas (REST surface; MCP server
              wrapper stubbed)
migrations/   forward-only alembic (0001: schema + RLS + pgvector + grants)
api/openapi.yaml, events/semantic_event_envelope.avsc
tests/unit/golden/   committed golden SQL per (case × dialect) — the compiler contract
```

## Dialect table (SEM-FR-023 + task scope)

Identifiers are ALWAYS quoted; filter values are ALWAYS `?` placeholders bound
by query-service — never literals. `first` is always deterministic (BR-8):
ordered by the entity primary key or the request's `order_within_group`;
`arbitrary()` is never emitted.

| Dialect  | Identifier quoting | `date_trunc(month, x)`   | `count_distinct` | `first(x)` (ordered by `k`)               | LIMIT            | GROUP BY |
|----------|--------------------|--------------------------|------------------|-------------------------------------------|------------------|----------|
| duckdb   | `"x"` (`""` esc)   | `date_trunc('month', x)` | `count(DISTINCT x)` | `arg_min(x, k)`                        | `LIMIT n`        | ordinals |
| trino    | `"x"` (`""` esc)   | `date_trunc('month', x)` | `count(DISTINCT x)` | `min_by(x, k)`                         | `LIMIT n`        | ordinals |
| athena   | `"x"` (`""` esc)   | `date_trunc('month', x)` | `count(DISTINCT x)` | `min_by(x, k)`                         | `LIMIT n`        | ordinals |
| bigquery | `` `x` ``          | `date_trunc(x, month)`   | `count(DISTINCT x)` | `array_agg(x ORDER BY k LIMIT 1)[OFFSET(0)]` | `LIMIT n`  | ordinals |
| synapse  | `[x]` (`]]` esc)   | `DATETRUNC(month, x)`    | `count(DISTINCT x)` | **unsupported → 422** (no deterministic grouped-first) | `SELECT TOP n` | expressions repeated (T-SQL forbids ordinals) |

Determinism (BR-7): dimensions in request order, metrics in request order,
filters sorted by `(dimension, op, values)`, params in first-appearance order,
single-line single-space SQL ⇒ same request + model version + dialect is
**byte-identical** (basis of the compile cache and the AC-5 contract test).

## FR traceability

| FR | Status | Code | Tests |
|---|---|---|---|
| SEM-FR-001 models + versioning, one published serves compile | ✅ | `domain/services.py` (ModelService, VersionService), `store/*` | `test_models_api.py`, `integration/test_persistence_versioning.py` |
| SEM-FR-002 entity binding validated against dataset schema | ✅ | `domain/definition.py::validate_definition` | `test_models_api.py::test_submit_fails_on_broken_binding` |
| SEM-FR-003 dimensions (types, time grains) | ✅ | `domain/definition.py` | `test_compiler_golden.py`, `test_compile_api.py::test_unknown_grain` |
| SEM-FR-004 measures, agg whitelist + `count_distinct` + deterministic `first`, derived measures | ✅ | `domain/definition.py`, `compiler/compiler.py` | `test_agg_whitelist.py` (AC-4), `test_duckdb_execution.py` |
| SEM-FR-005 join paths, declared-only, fan-out rejected at authoring | ✅ | `definition.py`, `compiler.py::_resolve_joins` | `test_compile_api.py::test_ac9_ambiguous_join_path`, `test_duckdb_execution.py::test_join_path_dimension` |
| SEM-FR-006 restricted expression grammar → AST at save | ✅ | `domain/expr.py` | `test_expr_grammar.py`, `test_models_api.py::test_expression_not_allowed_at_save` |
| SEM-FR-007 publication guard: validation + DS approval, author ≠ approver, diff event | ✅ | `services.py::VersionService.approve` | `test_models_api.py::test_ac6_review_workflow` |
| SEM-FR-008 dataset.schema_changed → health, 409 MODEL_UNHEALTHY | ✅ | `events/consumer.py` | `test_consumer_health.py::test_ac7_*` |
| SEM-FR-009 (S) deprecation with successor, compile warns | ✅ | compiler warnings, `measure.deprecated` event | `test_compile_api.py::test_deprecated_measure_warns`, `test_consumer_health.py::test_chart_reverse_index...` |
| SEM-FR-020 POST /compile request/response shape | ✅ | `compiler/compiler.py`, `api/routes/compile.py` | golden suite, `test_compile_api.py::test_ac1_compile_shape` |
| SEM-FR-021 SQL shape, grain truncation, multi-metric CTEs | ✅ | `compiler.py::_compile_single/_compile_multi` | `multi_entity_cte__*` goldens, `test_duckdb_execution.py::test_multi_entity_cte_executes` |
| SEM-FR-022 safety rules a–f | ✅ | `compiler.py::normalize_request` + renderers | `test_injection.py` (AC-2/AC-3), `test_agg_whitelist.py` |
| SEM-FR-023 dialects w/ per-dialect templates | ✅ (superset: +athena/bigquery/synapse) | `compiler/dialects.py` | golden suite × 5 dialects |
| SEM-FR-024 ?validate=true dry-run | ✅ (real `HttpQueryServiceClient`; fake for unit tier) | `services.py::CompileService`, `adapters/query_client.py` | `test_compile_api.py::test_validate_true_runs_dry_run` |
| SEM-FR-025 (S) compile cache keyed (version, request hash, dialect) | ✅ in-process (Redis TODO) | `CompileService._cache` | `test_compile_api.py::test_compile_cache_hit...` |
| SEM-FR-026 (S) /compile/chart mapping, passthrough | ✅ | `compiler/chart.py` | `test_chart_mapping.py` (matrix), AC-13 tests |
| SEM-FR-040 verified queries + lifecycle, author ≠ approver | ✅ | `services.py::VerifiedQueryService`, `domain/sqlguard.py` | `test_verified_queries.py` |
| SEM-FR-041 semantic search (pgvector, tenant-filtered in SQL) | ✅ | `store/sql.py::search`, `store/memory.py` | `test_verified_queries.py`, `integration::test_pgvector_semantic_search` |
| SEM-FR-042 (S) candidate harvesting | ✅ | `/verified-queries/candidates` | `test_verified_queries.py::test_candidates_endpoint...` |
| SEM-FR-043 (S) re-validation on publish/schema change | ✅ | `VersionService._revalidate_verified_queries`, consumer | `test_consumer_health.py` |
| SEM-FR-060 bootstrap from chart configs + saved queries | ✅ (artifacts inline; 202+operation) | `domain/bootstrap.py`, `services.py::BootstrapService` | `test_bootstrap.py::test_ac10_*` |
| SEM-FR-061 (S) bootstrap idempotence, origins | ✅ | same | `test_ac10_bootstrap_idempotent_rerun_changes_nothing` |
| SEM-FR-062 (S) report shape | ✅ | `BootstrapDeriver.report` | `test_bootstrap.py` |
| SEM-FR-080 MCP read tools + JSON-Schema I/O, audited | ✅ REST facade (`/api/v1/tools/*`), audits to real Kafka via outbox; MCP-protocol server wrapper deferred (unreachable) | `mcp/facade.py`, `api/routes/tools.py` | `test_tools_api.py` |
| SEM-FR-081 dual-consumer byte-identity | ✅ | one CompileService for all entry points | **`test_tools_api.py::test_ac5_contract_byte_identical_sql_across_consumers`** |
| MASTER: RLS/isolation, envelope, pagination, idempotency, outbox, events, consumers+dedup | ✅ | `store/*`, `api/*`, `events/*`, migration 0001 | `test_isolation_authz.py`, `integration/*` |

## AC traceability

| AC | Test |
|---|---|
| AC-1 | `test_compile_api.py::test_ac1_compile_shape`, `test_compiler_golden.py::test_time_range_resolves_relative_bounds_ac8` |
| AC-2 | `test_injection.py::test_ac2_injection_values_stay_in_params`, `test_duckdb_execution.py::test_ac2_injection_attempt_is_inert_end_to_end` |
| AC-3 | `test_injection.py::test_ac3_evil_metric_name_rejected_by_regex_gate` |
| AC-4 | `test_agg_whitelist.py::test_ac4_non_whitelisted_agg_rejected_with_allowed_list` |
| AC-5 | `test_tools_api.py::test_ac5_contract_byte_identical_sql_across_consumers` (normative) |
| AC-6 | `test_models_api.py::test_ac6_review_workflow` |
| AC-7 | `test_consumer_health.py::test_ac7_schema_change_breaks_measure_but_not_others` |
| AC-8 | `test_compiler_golden.py::test_time_range_resolves_relative_bounds_ac8` |
| AC-9 | `test_compile_api.py::test_ac9_ambiguous_join_path`, `test_duckdb_execution.py::test_join_path_dimension` |
| AC-10 | `test_bootstrap.py::test_ac10_bootstrap_derives_draft_definition`, `::test_ac10_bootstrap_idempotent_rerun_changes_nothing` |
| AC-11 | `test_verified_queries.py::test_ac11_read_only_violation_rejected`, `test_consumer_health.py::test_ac11_approved_query_moves_to_pending_review_on_schema_break` |
| AC-12 | `test_verified_queries.py::test_ac12_search_is_tenant_scoped_and_audited` |
| AC-13 | `test_compile_api.py::test_compile_chart_passthrough_and_aggregate`, `test_chart_mapping.py::test_pie_chart_maps_single_dim_single_metric_with_meta_aggregate_type` |
| AC-14 | `test_isolation_authz.py::test_ac14_cross_tenant_compile_404_and_audited`, `integration/test_rls_isolation.py::test_ac14_api_cross_tenant_404_with_audit_row` |

## Adapter inventory (END STATE: real runtime, unit-only doubles)

`SEM_USE_REAL_ADAPTERS=true` (runtime default in deploy) wires every port to real
local infra — no stub is reachable from `app.main` (CONVENTIONS.md END STATE). The
doubles in the right column exist **only** for the unit tier (`mode="memory"`,
`use_real_adapters=false`) and are never reachable from the runtime container.

| Port | Real runtime adapter (wired by `container.py`) | Unit-test double (unit tier only) |
|---|---|---|
| `DatasetClient` | `HttpDatasetClient` (real httpx → dataset-service, SPIFFE header) | `StaticDatasetClient` (in-memory registry) |
| `QueryServiceClient` | `HttpQueryServiceClient` (real httpx → query-service dry-run) | `FakeQueryServiceClient` (deterministic verdicts) |
| `EmbeddingClient` | `OpenAIEmbeddingClient` (real `/v1/embeddings` → ai-gateway/Ollama `nomic-embed-text`, 768-d) | `LocalHashEmbedding` (deterministic hashing) |
| Event bus | `KafkaEventBus` (real Redpanda via `windrose_common.kafka`, tenant-keyed) | `InMemoryEventBus` |
| Consumer dedup | `RedisDedupStore` (real Redis SET NX, 24h TTL via `windrose_common.redisx`) | `InMemoryDedupStore` / `SqlDedupStore` |
| Consumer transport | `KafkaSemanticConsumer` (real consumer groups, 5-retry backoff, DLQ) driving the transport-agnostic handler | direct in-memory bus subscription |
| Event relay | `OutboxDispatcher` → `KafkaEventBus` (committed rows → real Redpanda, MASTER-FR-034) | in-process dispatch |
| AuthZ | `OpaAuthzClient` (real OPA data API + Redis projection, MASTER-FR-012) | `LocalScopeAuthz` (JWT scopes) |
| AuthN | `TokenVerifier` (RS256; real JWKS fetch when `jwks_url` set, static PEM in tests) | same (static PEM) |

**Remaining stub (out of scope, unreachable from runtime):** `app/mcp/facade.py::McpServer`
— the MCP-protocol server wrapper (tool-plane registration, BRD 13). It is never
instantiated by any wiring; the callable surface is the REST facade at
`/api/v1/tools/*` (`McpFacade`, fully real). It raises `NotImplementedError` only if
directly constructed, so no running service reaches it.

## Real-adapter integration tests

`tests/integration/test_real_adapters.py` exercises the real wiring against the dev
compose stack (auto-skips per endpoint when down):

- `test_ollama_embeddings_are_real_dense_768_vectors` — **Ollama** `nomic-embed-text`.
- `test_verified_query_pgvector_ann_search_with_real_embeddings` — **Ollama + pgvector** ANN.
- `test_semantic_event_publishes_to_real_kafka_and_is_consumed` — outbox → **Redpanda** → consumed.
- `test_opa_authz_decision_via_real_container` — **OPA** decision over the real Rego bundle + Redis.

## Deviations / notes

- `compile_log` monthly native partitioning deferred (TODO in migration);
  6-month retention is a retention-job concern.
- Definition >64KB object-storage offload (MASTER-FR-061) not implemented;
  a hard 256KB cap is enforced instead (422 `LIMIT_EXCEEDED`), DB CHECK included.
- Cross-tenant audit (`security.cross_tenant_denied`): under RLS a by-id miss is
  indistinguishable from a foreign tenant's id, so every by-id miss is audited
  (404 either way — no existence leak; AC-14 satisfied).
- MCP `compile_metric_sql` applies the agent limit ceiling (default 10 000):
  when it clamps, a `LIMIT_CLAMPED` warning is attached — requests with an
  explicit in-ceiling limit remain byte-identical with the chart path (AC-5).
- `first` on synapse → 422 (no deterministic grouped-first template; documented
  per-engine capability per SEM-FR-004).
- Prometheus `/metrics` exposition + OTel spans are TODO stubs (route exists).

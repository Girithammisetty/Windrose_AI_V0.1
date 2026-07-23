# BRD 06 — semantic-service

**Service:** semantic-service · **Language:** Python (FastAPI) · **Phase:** 2
**Inherits:** `00_MASTER_BRD.md` · **Architecture:** `../../DATACERN_PLATFORM_ARCHITECTURE.md` §5, §6, §8.4
**V1 sources mined:** `_SERVER_AGGREGATION_SPEC.md` (agg-fn whitelist, per-chart translation, identifier quoting, drilldown), `ido/app/models/{query,saved_query}.rb`, chart `config/meta` conventions (`config.x/y/dataseries`, `meta.ySeries[].aggregateType`, `meta.aggregate.type/checked`)

---

## 1. Overview

**Purpose.** semantic-service owns the governed **semantic layer**: per-workspace **semantic models** (entities bound to datasets, dimensions, measures with aggregation definitions, join paths) and **verified queries** (curated NL↔SQL pairs with an approval workflow). Its core product is the **compile API**: `(metric + dimensions + filters + time grain) → safe SQL`, executed by query-service. **One definition, two consumers:** chart-service renders every aggregating chart through compile, and the analytics agent answers NL questions through the same definitions and MCP read tools — a metric can never mean two different things in a chart and a chat answer.

**Business value.** In V1, aggregation semantics lived in three places at once: browser JavaScript (`aggregateData` in `ui-core/chart.js`), a proposed chart-service `ChartSqlTranslator` (aggregation spec), and ad-hoc saved SQL — with the agg-fn list, identifier quoting, and column allowlisting re-implemented per consumer. The rebuild moves the aggregation SQL translation rules of `_SERVER_AGGREGATION_SPEC.md` **into this service** as the single compiler, adds governance (definitions are reviewed, versioned, approved), and gives agents a grounded, injection-proof way to query metrics.

**In scope:** semantic model CRUD + versioning + publication; entities/dimensions/measures/join paths; verified queries + approval workflow; compile API with safety rules (agg whitelist, identifier quoting, column allowlisting, injection-proof filters); model bootstrap from existing chart configs and saved queries; MCP read tools; dual-consumer contracts.

**Out of scope:** SQL execution (query-service); chart rendering & chart CRUD (chart-service, BRD 07); NL→metric-request planning (analytics agent in agent-runtime; this service only provides tools/compile); metric alerting.

## 2. Actors & user stories

Personas: **Analytics Engineer (AE)** (authors models), **Data Steward (DS)** (approves), **Analyst (AN)**, **Chart-service (CH)**, **Analytics Agent (AG)**, **Dashboard-designer Agent (DA)**.

- **US-1** As an AE, I define an entity `orders` bound to dataset `Orders`, with dimensions (`region`, `order_date`) and measures (`revenue = SUM(order_total)`), so "revenue" is defined once for the whole workspace.
- **US-2** As an AE, I declare a join path `orders.customer_id → customers.id` so metrics can slice by customer dimensions without hand-written joins.
- **US-3** As a DS, I review a draft model version, see the diff against the published version, and approve or reject with a message; only published versions serve traffic.
- **US-4** As CH, I compile `{measure: revenue, dimensions: [region], filters: […]}` for a bar chart and execute the returned SQL via query-service — no SQL construction in chart-service at all.
- **US-5** As an AN in the copilot, I ask "monthly revenue in EMEA this year"; the agent calls `get_metrics`/`get_dimensions`, then `compile_metric_sql`, and shows the answer with the compiled SQL as citation.
- **US-6** As an AE, I curate a verified query from a real question the agent answered well; once approved it becomes preferred grounding for similar questions.
- **US-7** As an AE, I bootstrap a first-cut model from my workspace's existing chart configs and saved queries, then edit instead of starting blank.
- **US-8** As a DS, I deprecate measure `gmv` with successor `net_revenue`; compiles of `gmv` still work but warn, and dependent charts get flagged.
- **US-9** As DA, I read the model catalog to propose a draft dashboard whose every tile references governed measures.
- **US-10** As an AE, I test-compile a draft version against sample parameters and see the SQL + dry-run cost before publishing.

## 3. Functional requirements

### Semantic models & versioning
- **SEM-FR-001 (Must)** One or more semantic models per workspace: `{name, description, status}`. Model content is versioned: `draft → in_review → published | rejected`; exactly one `published` version serves compile per model; previous published versions remain readable (`?version=`).
- **SEM-FR-002 (Must)** **Entity:** `{name (snake_case, unique per model), dataset_urn, dataset_version_policy: latest|pinned(version_no), primary_key columns[], description}`. Binding validates the dataset exists and referenced columns exist in its schema (via dataset-service).
- **SEM-FR-003 (Must)** **Dimension:** `{name, entity, column | expr (restricted expression grammar), type: categorical|time|numeric|boolean|geo, time_grains? ⊆ (hour, day, week, month, quarter, year), description, synonyms[]}`. A time dimension must map to a `date`/`timestamp` column.
- **SEM-FR-004 (Must)** **Measure:** `{name, entity, agg ∈ (sum, avg, min, max, count, count_distinct, first), expr (column or restricted expression), filters? (measure-level), format hint, description, synonyms[]}`. The agg whitelist extends the V1 client/spec set (`sum/avg/min/max/count/first`) with `count_distinct`; `first` compiles per engine capability (e.g., Trino `min_by`/`arbitrary` deterministic ordering variant; documented per engine). **Derived measures**: `expr_metric` referencing other measures with `+ - * /` and safe division (`NULLIF` denominator).
- **SEM-FR-005 (Must)** **Join path:** `{name, from_entity, to_entity, join_type ∈ (left, inner), on: [{from_column, to_column}], cardinality ∈ (many_to_one, one_to_one)}`. Compile resolves multi-hop joins via declared paths only (no inferred joins); ambiguous paths require the request to name the path; fan-out joins (many_to_many) are rejected at authoring time.
- **SEM-FR-006 (Must)** Restricted expression grammar for `expr` (parsed to AST at save; anything outside the grammar — subqueries, UDFs, window fns, comments, semicolons, string literals containing quotes unescaped — → 422 `EXPRESSION_NOT_ALLOWED`):
  ```
  expr      := term (('+'|'-'|'*'|'/'|'%') term)*
  term      := column | literal | func | case | '(' expr ')'
  func      := ('coalesce'|'nullif'|'cast'|'date_trunc'|'extract'|'lower'|'upper'|'trim'|'concat'|'abs'|'round') '(' args ')'
  case      := 'CASE' ('WHEN' cond 'THEN' expr)+ ('ELSE' expr)? 'END'
  cond      := expr ('='|'!='|'>'|'>='|'<'|'<='|'IS NULL'|'IS NOT NULL') expr? | cond ('AND'|'OR') cond | 'NOT' cond
  column    := identifier matching ^[a-z][a-z0-9_]{0,62}$ and present in the bound dataset schema
  literal   := number | quoted string (escaped) | TRUE | FALSE | NULL
  ```
- **SEM-FR-007 (Must)** Version publication requires: all entity bindings valid against current dataset schemas, zero broken references, DS approval (`semantic.model.approve` permission, author ≠ approver). Publishing emits `semantic.model_published` with a machine-readable diff (added/removed/changed dims, measures, joins).
- **SEM-FR-008 (Must)** Consuming `dataset.events.v1 :: dataset.schema_changed`: published models referencing removed/retyped columns get `health: broken_refs[]`, dependents are notified via `semantic.model_health_changed`; compile of a broken measure → 409 `MODEL_UNHEALTHY` (others unaffected).
- **SEM-FR-009 (Should)** Deprecation of individual measures/dimensions with `successor` pointer; compile succeeds with `warnings:[DEPRECATED]`.

### Compile API
- **SEM-FR-020 (Must)** `POST /compile`: request `{model, metrics: [measure names] (≥1), dimensions?: [{name, grain?}], filters?: […], time_range?: {dimension, start, end | relative}, order_by?, limit?, having?}` → response `{sql, params[], engine_dialect, output_schema: [{name, type, role: dimension|measure}], provenance: {model_version, measures, dimensions}, warnings[]}`.
- **SEM-FR-021 (Must)** Compiled SQL shape (per aggregation spec, generalized): `SELECT <dims with grain-truncation> , <AGG(expr)> per metric FROM <entity table/join tree> WHERE <filters as parameter placeholders> GROUP BY <dims> [HAVING …] [ORDER BY …] [LIMIT n]`. Time grain uses `date_trunc('<grain>', col)`. Multi-metric requests spanning entities compile to joined CTEs on shared dimensions.
- **SEM-FR-022 (Must)** **Safety rules (injection-proof by construction):**
  (a) aggregation functions only from the whitelist — unknown agg names cannot occur because measures are dereferenced by name from the published model, never accepted as raw SQL;
  (b) **every identifier** (table, column, alias) is quoted with the target engine's quoting rules and must resolve to a model-declared column (column allowlist — the aggregation spec's open question 2 answered: yes, enforced);
  (c) **filter values are never interpolated** — compile emits parameter placeholders and a `params` array; query-service binds them (BRD 05 QRY-FR-003);
  (d) filter operators whitelist: `= != > >= < <= IN NOT IN BETWEEN LIKE IS NULL IS NOT NULL`; `LIKE` patterns are parameters too;
  (e) request field values (`metrics`, `dimensions`, `order_by`) must match `^[a-z][a-z0-9_]{0,62}$` and resolve in the model — no free-form strings reach SQL;
  (f) `limit` capped at 50 000; compile refuses requests with > 8 dimensions or > 20 metrics.
- **SEM-FR-023 (Must)** Dialects: `trino` (primary), `duckdb`; the target is chosen by the caller (query-service routing decides the engine; compile is deterministic per dialect). Identifier quoting: double quotes both, escaping per dialect; `first` and `count_distinct` have per-dialect templates.
- **SEM-FR-024 (Must)** `POST /compile?validate=true` additionally runs query-service dry-run and returns cost estimate + ceiling verdict (used by agents and the model editor).
- **SEM-FR-025 (Should)** Compile result cache keyed `(model_version, canonical_request_hash, dialect)` — deterministic compiler makes this safe; invalidated on publish.
- **SEM-FR-026 (Should)** Chart-shape helper: `POST /compile/chart` accepts the chart-service contract `{chart_type, x, y[], dataseries?, agg per y}` and maps it onto compile (pie: single dim+metric; bar/line: per-Y agg via `ySeries` equivalents; line adds `ORDER BY` dims; scatter raw mode returns `passthrough: true` telling chart-service to use query-service directly, sankey likewise — V1 spec §3 behavior preserved).

### Verified queries
- **SEM-FR-040 (Must)** Verified query: `{nl_text, sql (parameterized, AST-validated read-only), variables[] (typed, as BRD 05), model?, tags[], embedding}`. Lifecycle `draft → pending_review → approved | rejected | archived`; only `approved` are served to agents. Approver permission `semantic.verified_query.approve`; author ≠ approver; every decision records actor + timestamp + optional message.
- **SEM-FR-041 (Must)** `GET /verified-queries:search {q, top_k ≤ 10}` — semantic search (pgvector embeddings) over approved pairs, returning similarity scores; the analytics agent's first grounding stop.
- **SEM-FR-042 (Should)** Candidate harvesting: `POST /verified-queries/candidates` (called by eval-service/agent-runtime for highly-rated agent answers) creates `draft` entries with provenance `{agent_run_urn}`.
- **SEM-FR-043 (Should)** On model publish, approved verified queries are re-validated (referenced columns still exist); breakage moves them to `pending_review` with `health_note`.

### Bootstrap
- **SEM-FR-060 (Must)** `POST /models/{id}/bootstrap {sources: [chart_configs, saved_queries], workspace}` (async, 202): reads chart configs from chart-service (V1-style `config.x/y/dataseries`, `meta.ySeries[<col>].aggregateType`, `meta.aggregate.type`) and saved queries from query-service; produces **draft** entities/dimensions/measures: each distinct dataset → entity; each `config.x`/`dataseries` column → dimension; each `(y column, aggregateType)` pair → measure named `<agg>_<column>` (dedup by expression); GROUP BY columns and aggregate expressions parsed from saved-query SQL contribute likewise. Output is a bootstrap report `{created[], skipped[], conflicts[]}` — never auto-published.
- **SEM-FR-061 (Should)** Bootstrap idempotence: re-running merges by expression identity, does not duplicate; user edits are never overwritten (draft items carry `origin: bootstrap|manual`, bootstrap only touches its own).
- **SEM-FR-062 (Should)** Bootstrap report shape:
  ```json
  {"operation_id":"018f…","status":"completed",
   "created":{"entities":3,"dimensions":14,"measures":9,"examples":["sum_order_total","avg_discount"]},
   "skipped":[{"source":"chart/018e…","reason":"passthrough chart_type sankey"}],
   "conflicts":[{"name":"sum_order_total","existing_expr":"sum(order_total)","candidate_expr":"sum(total)","action":"kept_existing"}]}
  ```

### MCP-facing read tools
- **SEM-FR-080 (Must)** MCP facade (read tier, per-workspace scoping from OBO token): `get_metrics(model?) → [{name, description, agg, entity, synonyms, deprecated?}]`; `get_dimensions(metric?|model?) → [{name, type, time_grains, sample_values? (top-10 from profile)}]`; `compile_metric_sql(request) → {sql, params, output_schema, provenance}` (compile with `validate=true`, agent ceilings); `search_verified_queries(q)`. All calls audited `ai.tool_invoked.v1`; tool schemas registered in tool-registry with version + deprecation window.
- **SEM-FR-081 (Must)** Dual-consumer guarantee: chart-service `GET /charts/:id/data` and the agent's `compile_metric_sql` MUST route through the same published model version and the same compiler; a contract test compiles an identical request via both entry points and asserts byte-identical SQL.

## 4. Domain model & data

### 4.1 Tables (Postgres, RLS)

**semantic_models** — `id uuidv7 PK`, `tenant_id`, `workspace_id`, `name`, `description`, `published_version_id uuid NULL`, `health jsonb`, `created_by`, timestamps, `deleted_at`. `UNIQUE (tenant_id, workspace_id, lower(name))`.

**model_versions** — `id`, `tenant_id`, `model_id FK`, `version_no int`, `status text (draft|in_review|published|rejected|superseded)`, `definition jsonb NOT NULL (≤256KB → object-storage pointer above 64KB per MASTER-FR-061: `definition_ref` used when large)`, `diff jsonb`, `submitted_by`, `approved_by NULL`, `decision_note`, `published_at`, `created_at`. `UNIQUE (model_id, version_no)`. Immutable after leaving `draft`.

Normalized projections for query/health (rebuilt from the published definition): **entities** (`id, tenant_id, model_version_id, name, dataset_urn, version_policy, primary_key jsonb`), **dimensions** (`…, entity_id, name, column, expr_ast jsonb, dim_type, time_grains text[], synonyms text[], deprecated, successor`), **measures** (`…, entity_id, name, agg text CHECK (IN ('sum','avg','min','max','count','count_distinct','first')), expr_ast jsonb, filters_ast jsonb, synonyms text[], deprecated, successor`), **join_paths** (`…, from_entity_id, to_entity_id, join_type, on_pairs jsonb, cardinality`). Unique names per model version; indexes on `(tenant_id, model_version_id, name)`.

**verified_queries** — `id`, `tenant_id`, `workspace_id`, `model_id NULL`, `nl_text text`, `sql_text text`, `variables jsonb`, `status text`, `provenance jsonb`, `health_note text`, `embedding vector(1024)`, `submitted_by`, `approved_by`, `decided_at`, timestamps. Indexes: `(tenant_id, workspace_id, status)`, HNSW on `embedding` (tenant-filtered queries only — hard tenant predicate in every ANN search).

**compile_log** — `id`, `tenant_id`, `model_version_id`, `request_hash`, `request jsonb`, `caller_class`, `dialect`, `warnings jsonb`, `duration_ms`, `created_at`. Monthly partitions, 6-month retention (observability + bootstrap of eval datasets).

Plus `outbox`, `idempotency_keys`.

### 4.2 State machines

**Model version:**

| From | To | Trigger | Guard |
|---|---|---|---|
| draft | in_review | author submits | full validation green (bindings, expr ASTs, join graph acyclic, name uniqueness) |
| in_review | published | approver approves | approver ≠ author; `semantic.model.approve`; per-model advisory lock; previous published → `superseded` |
| in_review | rejected | approver rejects | decision note required |
| rejected | draft | author revises | creates no new version_no; content editable again |
| published | superseded | newer version published | automatic |

No content edits outside `draft`. `definition` is immutable from `in_review` onward.

**Verified query:** `draft → pending_review` (submit; SQL AST validation green) → `approved` (approver ≠ author) | `rejected`; `approved → pending_review` (schema-break re-validation, SEM-FR-043, with `health_note`); any → `archived` (terminal, excluded from search).

### 4.3 Error code catalog

`VALIDATION_FAILED` (422) · `UNKNOWN_METRIC` / `UNKNOWN_DIMENSION` / `UNKNOWN_GRAIN` (422) · `EXPRESSION_NOT_ALLOWED` (422 authoring) · `AMBIGUOUS_JOIN_PATH` (422, lists candidates) · `MODEL_NOT_PUBLISHED` (409) · `MODEL_UNHEALTHY` (409, lists broken refs) · `NOT_FOUND` (404, incl. cross-tenant) · `CONFLICT` (409: name, state transition, concurrent publish) · `PERMISSION_DENIED` (403: author-approver, missing approve scope) · `LIMIT_EXCEEDED` (422: >8 dims, >20 metrics, limit >50 000, definition size caps).

## 5. API specification (base `/api/v1`)

| Method & path | Purpose | Notable errors |
|---|---|---|
| `POST /models` · `GET /models` · `GET /models/{id}` · `PATCH /models/{id}` · `DELETE /models/{id}` | model CRUD | 409 name |
| `GET /models/{id}/versions` · `POST /models/{id}/versions` (new draft from published) · `PATCH /models/{id}/versions/{v}` (draft only) | versioning | 409 not draft |
| `POST /models/{id}/versions/{v}/submit` · `/approve` · `/reject` | review workflow | 403 author-approver, 409 state |
| `GET /models/{id}/definition?version=` | full published definition (ETag) | |
| `POST /compile` (`?validate=true`) · `POST /compile/chart` | compile | 422 UNKNOWN_METRIC/UNKNOWN_DIMENSION/EXPRESSION_NOT_ALLOWED/AMBIGUOUS_JOIN_PATH, 409 MODEL_UNHEALTHY |
| `POST /models/{id}/bootstrap` | async bootstrap | 202 operation |
| `POST /verified-queries` · `GET /verified-queries` · `PATCH /verified-queries/{id}` · `POST /verified-queries/{id}/submit|approve|reject|archive` | verified-query lifecycle | 403, 409 |
| `GET /verified-queries:search?q=&top_k=` | semantic search (approved only) | |

Example — compile:
```json
POST /api/v1/compile
{"model":"sales","metrics":["revenue"],"dimensions":[{"name":"order_month","grain":"month"},{"name":"region"}],
 "filters":[{"dimension":"region","op":"IN","values":["EMEA","AMER"]}],
 "time_range":{"dimension":"order_date","relative":"last_12_months"},"limit":1000,"dialect":"trino"}
→ 200 {"data":{"sql":"SELECT date_trunc('month', \"o\".\"order_date\") AS \"order_month\", \"o\".\"region\" AS \"region\", sum(\"o\".\"order_total\") AS \"revenue\" FROM \"bronze\".\"t42\".\"ds_orders\" \"o\" WHERE \"o\".\"region\" IN (?, ?) AND \"o\".\"order_date\" >= ? AND \"o\".\"order_date\" < ? GROUP BY 1, 2 ORDER BY 1 LIMIT 1000",
 "params":[{"type":"string","value":"EMEA"},{"type":"string","value":"AMER"},{"type":"date","value":"2025-07-01"},{"type":"date","value":"2026-07-01"}],
 "output_schema":[{"name":"order_month","type":"date","role":"dimension"},{"name":"region","type":"string","role":"dimension"},{"name":"revenue","type":"decimal","role":"measure"}],
 "provenance":{"model_version":"sales@v7","measures":["revenue"]},"warnings":[]}}
```

Example — chart-shape compile (chart-service consumer):
```json
POST /api/v1/compile/chart
{"model":"sales","chart_type":"vertical_bar_chart","x":"region",
 "y":[{"measure":"revenue"},{"measure":"avg_order_value"}],"dataseries":null,
 "filters":[{"dimension":"order_year","op":"=","values":[2026]}],"dialect":"trino"}
→ 200 {"data":{"sql":"SELECT \"o\".\"region\" AS \"region\", sum(\"o\".\"order_total\") AS \"revenue\", avg(\"o\".\"order_total\") AS \"avg_order_value\" FROM … WHERE … GROUP BY 1","params":[{"type":"integer","value":2026}],
 "output_schema":[{"name":"region","type":"string","role":"dimension"},
                  {"name":"revenue","type":"decimal","role":"measure"},
                  {"name":"avg_order_value","type":"decimal","role":"measure"}],
 "passthrough":false,"provenance":{"model_version":"sales@v7"}}}
```

Example — MCP tool `get_metrics` result (agent-facing):
```json
{"metrics":[{"name":"revenue","description":"Gross order revenue","agg":"sum","entity":"orders",
             "synonyms":["sales","gross revenue"],"deprecated":false},
            {"name":"gmv","agg":"sum","entity":"orders","deprecated":true,"successor":"net_revenue"}],
 "model_version":"sales@v7"}
```

Example — verified query lifecycle:
```json
POST /api/v1/verified-queries
{"nl_text":"monthly revenue by region for the last year",
 "sql_text":"SELECT date_trunc('month', order_date) m, region, sum(order_total) FROM {{dataset('Orders')}} WHERE order_date >= :start GROUP BY 1,2",
 "variables":[{"name":"start","type":"date","required":true}],"model":"sales","tags":["revenue"]}
→ 201 {"data":{"id":"018f…","status":"draft"}}
POST /api/v1/verified-queries/018f…/submit  → 200 {"status":"pending_review"}
POST /api/v1/verified-queries/018f…/approve → 200 {"status":"approved","approved_by":"u-77","decided_at":"…"}
```

## 6. Events

**Emitted → `semantic.events.v1`:** `model.created/updated/deleted`, `model.version_submitted`, `model.version_published {version_no, diff}`, `model.version_rejected`, `model.health_changed {broken_refs[]}`, `measure.deprecated`, `verified_query.submitted/approved/rejected/archived`, `bootstrap.completed {created_counts}`.

**Consumed:** `dataset.events.v1 :: dataset.schema_changed / dataset.deleted` → recompute health of published versions binding that dataset (SEM-FR-008), re-validate verified queries; `chart.events.v1 :: chart.created/updated` → maintain reverse index of chart→measure references (for deprecation impact); `rbac.events.v1 :: workspace.deleted` → soft-delete models. Consumers idempotent + DLQ per master.

## 7. Business rules & edge cases

- **BR-1** Compile never accepts SQL fragments from callers; the only free-text field anywhere in the compile request is filter **values**, and those exit only as bound parameters.
- **BR-2** A compile against a model with no published version → 409 `MODEL_NOT_PUBLISHED`; drafts compile only via the authoring test endpoint with `X-Draft-Version` header and `semantic.model.write` permission.
- **BR-3** `count` with no `expr` compiles to `count(*)`; `count_distinct` requires a column/expr; `avg` of a non-numeric column is rejected at authoring, not compile.
- **BR-4** Requests mixing dimensions reachable only through different join paths from the metric's entity → 422 `AMBIGUOUS_JOIN_PATH` listing candidates; caller may pin `join_path`.
- **BR-5** Time grain requested but dimension lacks that grain → 422; `relative` time ranges resolve at compile time in the tenant's reporting timezone (workspace setting, default UTC) and return the resolved absolute bounds in `provenance`.
- **BR-6** Two measures with identical names in one model version cannot exist (authoring 409); synonyms must not collide with names of other objects in the model.
- **BR-7** Deterministic output: same request + model version + dialect ⇒ identical SQL. Canonicalization before compile: dimensions sorted by request order then name for GROUP BY ordinals; metrics in request order; filters sorted by (dimension, op); params ordered by first appearance in SQL; whitespace normalized. Required for caching (SEM-FR-025), eval scoring, and the SEM-FR-081 contract test.
- **BR-8** `first` agg: compile orders by the entity's declared primary key unless the request supplies `order_within_group`; nondeterministic `arbitrary()` is never emitted.
- **BR-9** Entity `dataset_version_policy: pinned` compiles `FOR VERSION AS OF` (Iceberg) via query-service dataset-ref syntax; `latest` uses the current version at execution time.
- **BR-10** Model publish concurrency: publishing takes a per-model advisory lock; two simultaneous approvals cannot both become `published`.
- **BR-11** Verified-query SQL passes the same AST read-only classification as query-service (single SELECT, no DDL/DML) at save time; approval re-runs validation.
- **BR-12** Bootstrap never overwrites `origin: manual` objects and never touches non-draft versions; conflicts (same name, different expr) are reported, not merged.
- **BR-13** Chart passthrough types (`sankey`, raw-mode `scatter`, `grid`) are explicitly not compiled — `/compile/chart` returns `passthrough: true` so chart-service uses the saved query directly (V1 spec §3.4–3.5 semantics).
- **BR-14** Embedding search results are strictly tenant+workspace filtered in SQL (not post-filtered); an empty result set is returned rather than relaxing the filter.

## 8. Dependencies

- **Upstream:** dataset-service (schema validation, profile `top_values` for `sample_values`), query-service (dry-run validation), chart-service + query-service read APIs (bootstrap), ai-gateway (embeddings for verified queries), identity/rbac/OPA, pgvector, Kafka, Redis (compile cache), tool-registry (MCP registration).
- **Downstream:** chart-service (compile + compile/chart consumer; renders only through this service for aggregating charts); analytics agent & dashboard-designer agent via MCP tools; eval-service (golden NL→SQL datasets sourced from verified queries + compile_log); bff-graphql (model editor UI).
- **Contract:** compile `output_schema` is the authoritative column contract chart-service uses to render — columns/types/roles, matching the aggregation spec's response shape (`columns/column_types/rows/aggregated:true` at chart-service edge).

### Dual-consumer flow (normative)

```
chart-service:   GET /charts/:id/data ──► POST /compile/chart ──► sql+params ──► query-service /sql/run (Arrow) ──► buckets → UI
analytics agent: NL question ──► search_verified_queries ──hit──► run via query-service (agent ceilings)
                              └─miss─► get_metrics/get_dimensions ──► compile_metric_sql (validate=true) ──► run ──► answer + SQL citation
```
Both paths resolve the same published `model_version`; provenance is attached to every chart payload and agent answer.

## 9. NFRs (deltas from master)

- Compile p95 ≤ 150ms (cache hit ≤ 20ms); compile+validate (with query-service dry-run) p95 ≤ 1s.
- `get_metrics`/`get_dimensions` p95 ≤ 100ms (Redis-cached published definitions).
- Verified-query semantic search p95 ≤ 200ms at 100K entries/tenant.
- Definition size limits: ≤ 500 measures, ≤ 500 dimensions, ≤ 100 entities, ≤ 200 join paths per model version (validation errors beyond).

## 10. Acceptance criteria

- **AC-1** Given a published model with measure `revenue = sum(order_total)`, when compiling `{metrics:[revenue], dimensions:[region]}` for trino, then the SQL contains `sum("o"."order_total")`, all identifiers double-quoted, `GROUP BY` on the dimension, and zero literal filter values.
- **AC-2** Given filters `region IN ["EMEA","AMER"]` and a value containing `"; DROP TABLE--`, when compiled and executed via query-service, then values appear only in `params[]`, the engine log shows placeholders, and no injection occurs (end-to-end contract test).
- **AC-3** Given a request naming measure `evil(); --`, then 422 `UNKNOWN_METRIC` from the `^[a-z][a-z0-9_]{0,62}$` gate before any model lookup.
- **AC-4** Given an agg value outside the whitelist submitted at authoring (`agg: "exec"`), then 422 with the allowed list (`sum, avg, min, max, count, count_distinct, first`).
- **AC-5** Given the same compile request issued by chart-service and via the MCP `compile_metric_sql` tool, then the returned SQL strings are byte-identical and cite the same `model_version` (SEM-FR-081 contract test).
- **AC-6** Given a draft version submitted by user X, when X attempts approval, then 403; when steward Y approves, the version becomes `published`, the prior published becomes `superseded`, and `model.version_published` carries the diff.
- **AC-7** Given `dataset.schema_changed` removing `order_total`, then the model's health lists the broken measure, compile of `revenue` → 409 `MODEL_UNHEALTHY`, compile of unaffected metrics succeeds, and `model.health_changed` is emitted.
- **AC-8** Given a monthly grain request on time dimension `order_date` with `relative: last_12_months`, then SQL uses `date_trunc('month', …)`, the WHERE bounds are parameterized, and `provenance` reports the resolved absolute range in the workspace timezone.
- **AC-9** Given dimensions from `customers` requested with a metric on `orders` and a declared many-to-one join path, then compile emits the declared LEFT JOIN with quoted on-clause columns; with two candidate paths and none pinned, 422 `AMBIGUOUS_JOIN_PATH`.
- **AC-10** Given bootstrap over a workspace with 12 charts (pie/bar/line configs with `ySeries.aggregateType`) and 8 saved queries, then draft measures/dimensions are created with `origin: bootstrap`, duplicate expressions dedup to one measure, a report lists created/skipped/conflicts, and re-running changes nothing.
- **AC-11** Given a verified query whose SQL contains `UPDATE`, when saved, then 422 read-only violation; given an approved one whose referenced column is later dropped, then it moves to `pending_review` with a `health_note`.
- **AC-12** Given `search_verified_queries("revenue by region monthly")` from an agent OBO token of workspace W, then only approved entries of W's tenant+workspace return, ranked by similarity, and the call is audited as `ai.tool_invoked.v1`.
- **AC-13** Given `/compile/chart` for `chart_type: sankey`, then the response is `passthrough: true` with no SQL; for `pie_chart` with `meta.aggregate.type: avg`, the compiled SQL aggregates with `avg` on the single metric.
- **AC-14** Given tenant A's token compiling against tenant B's model id, then 404 + `security.cross_tenant_denied` audit event.

## 11. Out of scope / future

Metric alerting/subscriptions; caching of compiled **results** (query-service owns result cache); dbt-project import; cross-workspace shared models; row-level security predicates inside the semantic layer (RLS stays at data platform level); natural-language model authoring (agent proposals may draft definitions later via the proposal flow); additional dialects (BigQuery/Synapse direct) beyond trino/duckdb.
Future chart types needing non-GROUP-BY translation (whisker percentiles, histogram `width_bucket`, waterfall window functions — aggregation spec §8) land as compiler extensions here, never in chart-service.

# BRD 58 — Production Hardening (5A)

**Status:** in-progress — 2026-07-21 · increments landed where noted
**Owner:** platform · **Related:** [scalability-audit](../initiatives/scalability-audit.md), [stability-durability](../initiatives/stability-durability.md), memories `project_datacern_scalability_audit`, `project_datacern_stability_doctor`

The gap between "advanced beta / pilot-capable" and "customer-installable" is
almost entirely operationalization, not features. This BRD is the sequenced
program to close it. Each workstream follows Analysis → Design → Implement → Test.

---

## WS1 — Security fast-follows

### Analysis
**Product:** a security review / pentest must pass before any customer install. Two
findings are blocking-class; the rest are defense-in-depth.
**Technical (audited):**
- **SEC-1 (blocking): superuser dev-default DSNs → silent RLS bypass.** `case-service/cmd/server/main.go:68`, `tool-plane/cmd/{gateway,registry}/main.go`, `query-service/cmd/server/main.go:61` default to a SUPERUSER/BYPASSRLS role. A single unset `DATABASE_URL` in prod defeats *all* tenant isolation with no guard. No runtime self-check exists (only integration tests assert it).
- **SEC-2 (blocking): audit→WORM delivery not guaranteed** — hash-chain + WORM are strong, but delivery depends on dynamic topic-discovery + hourly seal; a prior incident lost 147 `case.events.v1` while the consumer looked healthy.
- **SEC-3: no CSP/HSTS/X-Frame/X-Content on the main app; BFF has no CORS allowlist** (`ui-web/src/middleware.ts:69` embed-only; `bff-graphql/src/index.ts:64`).
- **SEC-4: agent-runtime migrations 0006/0007/0012 regressed off the `NULLIF()` RLS form** — still fail-closed but re-introduces the pooled-connection availability bug 0005 fixed.
- **SEC-5: residual injection edges** — DNS-rebind TOCTOU in SSRF guard; string-built SQL on DuckDB browse + BigQuery driver; regex-only PII redaction.

### Design
- **SEC-1:** add `AssertNonSuperuser(ctx, pool)` to `libs/go-common` + `assert_non_superuser()` to `libs/py-common`; run `SELECT rolsuper, rolbypassrls` at boot and **refuse to start** if either is true (env-gated `DB_REQUIRE_NONSUPERUSER=true`, default true in prod profile). Change the four flagged DSN defaults to the `*_app` role name.
- **SEC-2:** static topic subscription list + a boot reconcile that replays unsealed days; alert if `now - last_sealed > 2h`.
- **SEC-3:** security-headers middleware in ui-web + an explicit CORS allowlist + helmet-style headers on the BFF.
- **SEC-4:** forward-only migrations re-remediating to `NULLIF(current_setting('app.tenant_id', true), '')::uuid`.
- **SEC-5:** re-resolve+pin IP in the SSRF connector; identifier allow-listing on the two string-SQL drivers; leave regex PII (documented floor) + add name/address patterns.

### Implement
- [x] **SEC-1** boot self-check — see Implementation & Test log below (this BRD's first landed increment).
- [ ] SEC-2 audit delivery reconcile · [ ] SEC-3 headers/CORS · [ ] SEC-4 NULLIF re-remediation · [ ] SEC-5 injection edges

### Test
Unit test on the self-check helper (superuser role → refuse; app role → pass);
integration test already asserts `rolsuper=false`. Live: boot with a superuser DSN
must fail closed.

---

## WS2 — Operational layer (observability you can actually operate)

### Analysis
**Product:** in production you must *see* and *be alerted*. Today the platform is
instrumented but operationally blind.
**Technical (audited):** full RED metrics on every service (strong); OTel tracing
wired but **off by default** and **Kafka doesn't propagate span context**
(`libs/go-common/kafka/producer.go:109` injects only a UUID); collector exports to
stdout only; **zero Grafana dashboards, zero alert rules, zero SLOs**; ServiceMonitor
disabled by default (`deploy/helm/.../values.yaml:246`).

### Design
- Turn tracing on in the prod Helm profile; add W3C `traceparent` inject/extract to the Kafka producer/consumer wrappers so async traces join.
- Deploy a trace backend (Tempo) + wire the collector to it (replace `[debug]`).
- Ship a dashboards-as-code bundle (Grafana JSON) for the RED metrics + per-service SLOs; a `PrometheusRule` set (error-rate, latency, saturation, consumer-lag, outbox-depth, audit-seal-age).
- Trace-id correlation onto every log line (extend the JSON logging middleware).

### Implement / Test
- [ ] Kafka trace propagation (+ unit test asserting extract==inject) · [ ] Tempo + collector wiring · [ ] Grafana dashboards + PrometheusRule bundle · [ ] SLO doc · [ ] log trace-id correlation.

---

## WS3 — Cloud bring-up (the #1 turnkey blocker)

### Analysis
**Product:** the platform has **never run on real cloud infra** — no `tfstate`, TF
authored for 4 clouds but only Hetzner ever `init`'d. Cannot install for a customer
until one cloud is proven end to end.
**Technical (audited):** Helm chart is production-shaped (all 23 svcs, probes, ESO
secrets, NetworkPolicies). Gaps: **no managed-Postgres DB/role bootstrap** (~20 DBs +
NOBYPASSRLS app roles presumed to exist; only Hetzner creates them); OpenSearch not
provisioned as managed in cloud TF; HPA templated but unconfigured.

### Design
- A `bootstrap` Helm hook Job (or TF module) that creates the ~20 databases + per-service `*_app` NOSUPERUSER NOBYPASSRLS roles on managed Postgres before the migrate jobs run.
- Add a managed OpenSearch/ClickHouse module per cloud (or a supported managed vendor).
- Set `autoscale` in the prod values for the stateless tiers (HPA min/max/targetCPU).
- Apply TF on ONE cloud (AWS first), run the CD workflow, prove `make doctor` green in-cluster.

### Implement / Test
- [x] managed OpenSearch module (AWS only — see B9/B10 log below; GCP/Azure have no native managed equivalent).
- [ ] DB/role bootstrap job · [ ] managed ClickHouse module (no native offering on any cloud) · [ ] HPA values · [ ] **apply on AWS + prove rollout** (needs a cloud account — resource-gated, not code-gated).

---

## WS4 — Scalability blockers (from the audit; gates millions/tenant)

### Analysis / Design
Full analysis in [scalability-audit](../initiatives/scalability-audit.md). Priority:
1. **B1+B2** streaming Iceberg commit + hard upload size/row cap (`libs/py-common/.../iceberg.py:108`, `ingestion-service/app/config.py`).
2. **B6+B7** retention reapers — prune published outbox rows; TTL `processed_events` (+ index). Template: usage-service `EnforceRetention`.
3. **B3** wrap `ExecSQL` with the caller's LIMIT for all callers.
4. **B9+B10** provision ClickHouse/OpenSearch HA (overlaps WS3).
5. **B5** bulk `_bulk` reindex + `(tenant_id,created_at)` index (also fixes the self-heal OOM).

### Implement / Test
- [x] **B2** upload size/row cap · [x] **B7** `processed_events` retention + index
  · [x] **B6** outbox reaper · [x] **B3** LIMIT-all-callers · [x] **B1** streaming commit
  · [x] **B5** bulk reindex + `(tenant_id,created_at)` index · [x] **B9/B10** (partial — see log below)
  · [x] **B4** DuckDB view materialization · [x] **B8** batch chain-append + batch ClickHouse
  insert (see log below).
  Still open: the full B9/B10 ClickHouse-HA + GCP/Azure managed-search parity, and
  the RISK-tier items (see [scalability-audit](../initiatives/scalability-audit.md)).

---

## WS5 — Test & release confidence

### Analysis / Design
No coverage gates in any language; no contract testing; live-e2e is real but the
default runner flakes. Add: per-language coverage thresholds (start low, ratchet);
GraphQL schema-snapshot + event-envelope conformance as CI gates; a load/soak target
(`make soak` exists for restart; add a volume load test at 1M rows for WS4 items).

### Implement / Test
- [x] coverage thresholds · [x] schema-snapshot gate · [x] event-envelope conformance
  (shared validator; adopted by all 19 emitting services) · [x] 1M-row load test
  harness — see log below.

---

## Implementation & Test log (landed increments)

### SEC-1 — non-superuser RLS boot check — DONE
`libs/go-common/dbcheck` (`AssertNonSuperuser` + pure `decide`/`strict`); wired into
the 4 flagged services (case-service, query-service, tool-plane gateway+registry)
right after pool creation. Default = **warn** (local dev on the superuser DSN keeps
booting); `DB_REQUIRE_NONSUPERUSER=true` = **hard refuse** (set in prod Helm
`values.yaml config:` next to `REQUIRE_REAL_ADAPTERS`). Local note added in
`deploy/e2e/config.env`.
**Test:** `go test ./dbcheck/` green (decision matrix: app-role→ok, superuser/bypass
→refuse-when-strict / warn-when-lax; env-gate). All 4 services `go build` clean.
Live boot-refusal against a superuser DSN with the flag on = deferred to the WS3
cloud bring-up (needs the app-role DSN).

### B2 — upload total-size / part-count cap — DONE
`ingestion-service` config `max_upload_bytes` (5 GiB) + `max_upload_parts` (10k);
`enforce_upload_caps()` extracted as a pure function, called in `UploadService.complete()`
BEFORE the memory-bound commit so an oversized upload fails fast (HTTP 400) instead
of OOMing. 0 = unlimited.
**Test:** `tests/unit/test_upload_caps.py` (5 cases: within/over-bytes/over-parts/
unlimited/boundary) green; full ingestion unit suite **535 passed**; ruff clean.

### B6/B7 — retention reapers (outbox + processed_events) — DONE

**A real correctness bug found via testing, not assumed away:** outbox tables
have RLS (FORCE ROW LEVEL SECURITY) with a tenant-scoped policy. A plain
cross-tenant DELETE with no session context matches ZERO rows — not an error,
silently useless — the write-path twin of what SEC-1 guards against for reads.
Every service's own outbox relay already opens this door with a `set_config`
GUC before querying, and the GUC **differs per service** (verified in code, not
assumed from one example): `app.role='platform'` for case/chart/notification/
query/usage/identity/tool-plane; `app.worker='on'` for rbac-service;
`app.worker='true'` for dataset-service/memory-service. ingestion-service uses
neither — its relay bypasses RLS via two narrow SECURITY DEFINER SQL functions
(migration 0005), so a plain DELETE there needed a matching function, not a GUC.
`processed_events` had NO cross-tenant policy at all in dataset-service or
memory-service — a background sweep would have silently pruned nothing.

**Go (`libs/go-common/outbox.Pruner`):** batched DELETE via `pgx.BeginFunc`,
re-asserting `PlatformGUC`/`PlatformVal` inside the same transaction as each
batch (constructor requires both — no accidental silent-no-op default). Wired
into all 8 Go outbox owners: case-service, chart-service, notification-service,
query-service, rbac-service, usage-service, identity-service, tool-plane
(gateway + registry) — each with its verified-correct GUC.
**Test:** `go test ./outbox/...` — 10 cases incl. batching, GUC-set-before-delete
assertion, unsafe-identifier rejection, no-GUC-skips-set_config. All 8 services
`go build`/`go test` clean (0 fails).

**Python (`libs/py-common/datacern_common/retention.py`):** `RetentionSpec` +
`prune_table`, same transaction-scoped `worker_guc`/`worker_val` re-assertion
per batch. Wired into dataset-service (outbox + processed_events) and
memory-service (outbox + processed_events), each hourly.
**New migrations** (forward-only, mirroring each service's own `worker_outbox`
precedent): dataset-service `0005_processed_events_worker_policy.py`,
memory-service `0003_processed_events_worker_policy.py` — grant
`app.worker='true'` cross-tenant access to `processed_events`, which previously
had none. Both remain single alembic heads (`alembic heads` verified).
**Test:** `test_retention.py` — 15 cases incl. worker-GUC-set-before-delete,
no-GUC-skips-set_config, unsafe-GUC-rejection. Ruff clean; dataset-service 214
passed, memory-service 43 passed (full unit suites, 0 fails).

**ingestion-service (bespoke — the generic helper doesn't apply):** new
migration `0009_outbox_prune_fn.py` adds `ing_outbox_prune(retention_seconds,
batch)`, a SECURITY DEFINER function matching 0005's `ing_outbox_claim_pending`/
`ing_outbox_mark_published` precedent exactly. New `prune_pending()` in
`app/events/outbox.py` calls it on Postgres, plain DELETE on SQLite (unit tier).
**Test:** `test_outbox_prune.py` — 4 cases against real SQLite (old-published
pruned, recent kept, **unpublished rows survive regardless of age** — only
delivered events are safe to drop). Full ingestion-service suite 539 passed.

**Deferred, explicitly (not silently dropped):** processed_events on the other
6 Python owners (ai-gateway, eval-service, experiment-service, inference-service,
pipeline-orchestrator, semantic-service) needs the identical
worker-policy-migration + wiring pattern established here — mechanical, same
shape, not yet applied. rbac-service's `outbox` table is Go (already covered,
`app.worker='on'`) — it has no `processed_events` table. Live/soak verification
(does the GUC actually work against a real RLS-enforced Postgres, not just unit
fakes) is pending the next full-stack boot.

### B6/B7 amendment (2026-07-23) — the shared Python reaper's SQL never worked; found via live-Postgres verification — FIXED

While wiring the last remaining B7 owner (inference-service, below), the
first-ever run of `datacern_common.retention.prune_table` against a REAL
Postgres exposed that its DELETE statement was broken from the day it landed:
`sqlalchemy.text()`'s bind-param regex has a negative lookahead for `:`, so
the original `:retention_seconds::text` was silently NOT treated as a bind
param — the statement reached the driver with a literal colon and raised
`PostgresSyntaxError` on every invocation. Every wired service's hourly sweep
(dataset-service, memory-service since landing) failed and swallowed the error
via `logger.exception("retention prune failed")` — the exact silent-no-op
failure mode B6/B7 was meant to close. **Root cause of the test gap:** the
original `test_retention.py` used a fake async session (its own docstring says
so) — the SQL string was asserted on but never executed, violating the
no-fakes rule and letting unexecutable SQL ship.

**Fix:** `age_expr` rewritten to `now() - (interval '1 second' *
:retention_seconds)` (bind param not adjacent to a cast). **New
`libs/py-common/tests/test_retention_live.py`** — 4 tests against the REAL
dev-infra Postgres (conftest reachability-skip pattern), on a scratch table
with the exact FORCE-RLS shape `processed_events` has in every owner, run as a
scratch NON-superuser role so RLS genuinely applies: (1) no worker GUC → the
silent RLS no-op trap, 0 rows; (2) production spec → both tenants' aged rows
deleted, fresh kept, idempotent; (3) outbox shape → aged-but-unpublished rows
survive; (4) batch_size smaller than doomed set → sweeps until drained. The
15 fake-session logic tests are kept for batching/identifier-safety, but SQL
shape is now proven by execution, not string assertion.

### B7 — inference-service (the last unwired owner) — DONE

`processed_events` had only the tenant-isolation policy (0001) — a worker
sweep would RLS-no-op. New migration `0002_processed_events_worker_policy.py`
(exact mirror of the other owners); new `WorkerSet._retention_loop` in
`app/workers.py` (inference's own worker idiom), hourly, same specs as every
other owner (`outbox` 30d published-only + `processed_events` 48h), same
`app.worker='true'` GUC its own worker sessions already use (`store/sql.py`).
**Live-verified end-to-end on real Postgres:** fresh `inference` DB, alembic
0001→0002 applied, seeded 2 tenants' aged rows + 1 fresh row, ran
`prune_table` as the real non-superuser `inference_app` role: without GUC →
0 deleted (trap proven), with GUC → exactly 2 deleted, second sweep → 0,
fresh row intact. Full inference-service suite 52 passed; ruff clean;
`alembic heads` single head (0002).

### B3 — wrap ExecSQL with the caller's LIMIT for all callers — DONE

**Root cause confirmed, not assumed:** `query-service/internal/exec/plan.go`'s
LIMIT-injection block only fired `if req.Op.Caller == domain.CallerAgent`.
Checked the actual caller — `chart-service/internal/resolve/clients.go:226,231`
already sends `"limit": limit` on **every** `/sql/run` call, and
`handlers_sql.go:55` already threads it into `PlanRequest.Limit` — the intended
result-set size was captured all the way to the plan and then silently
discarded for non-agent callers. A chart matching millions of rows executed in
full (bounded only by the much looser `MaxResultRows=5M`/`MaxResultBytes=1GB`)
to display a few thousand.

**Fix:** split the block — `DryRunForced` stays agent-only (an unrelated
governance property); LIMIT injection now applies whenever `req.Limit > 0`
**for any caller class**, with agents additionally getting a mandatory
`AgentInjectedLimit` ceiling even with no/looser requested limit (defense in
depth for the least-trusted caller — exact prior agent behavior preserved,
verified byte-for-byte against the existing `TestBrokerAgentHardening`). A
non-agent caller that requests no limit is left exactly as before (still
bounded by `MaxResultRows`/`MaxResultBytes` elsewhere) — this closes only the
gap where a limit **was** requested and got ignored.

**Test — TDD, bug reproduced before the fix:** added
`TestBrokerServiceCallerLimitHonored` to the existing `broker_test.go` fixture;
ran it against the unfixed code first and confirmed it **fails** exactly as
predicted (`"...orders_v3\"" does not contain "LIMIT 5000"`), then applied the
fix and confirmed it passes, alongside the pre-existing
`TestBrokerAgentHardening` (unchanged, still green) — proves the agent path
wasn't touched. Full `query-service` suite (incl. integration): all packages
`ok`, 0 fails. `go vet`/`gofmt` clean.

### B1 — stream the Iceberg commit instead of materializing the whole staged file — DONE

**Root cause confirmed, not assumed:** `IcebergTableWriter.stage()`
(`libs/py-common/datacern_common/iceberg.py`) already streams the decoded rows
into a temp parquet file in bounded batches — that half was fine. `commit()`
was the actual ceiling: `_arrow_string_table()` did `pq.read_table(path)`
(whole file into one arrow Table) then `.cast(...)` (a second full copy), and
`_commit_sync` cast the result a *third* time before handing it to
`tbl.append()` — three full in-memory copies of the staged file before
pyiceberg's own internal chunking (`bin_pack_arrow_table`) ever ran. With
`max_running_per_tenant=5` concurrent ingestions, this is exactly the OOM
ceiling the scalability audit flagged (stage streams fine, commit doesn't).

Surveyed pyiceberg 0.9.1's write surface before choosing a fix:
`Table.append()`/`Transaction.append()` hard-require a fully-materialized
`pa.Table` (`isinstance` gate); `Table.add_files()` is genuinely zero-copy but
needs the source files already at the table's own warehouse location —
`stage()` writes to local `/tmp` on the ingestion-service pod, so wiring it up
would mean relocating where `stage()` writes, a bigger change out of scope
here; the private `_dataframe_to_data_files()` that `append()` delegates to
also takes a materialized `pa.Table` as input in every path inspected.

**Fix:** replaced the single whole-file read+cast+append with
`_iter_string_chunks()`, which streams the staged parquet file via
`pq.ParquetFile(path).iter_batches(batch_size=commit_chunk_rows)` and casts
each bounded chunk to the Iceberg schema individually; `_commit_sync` now
calls `tbl.append()` once per chunk instead of once for the whole file. Peak
memory at commit is now O(`commit_chunk_rows` rows) instead of O(whole staged
file), regardless of ingestion size. `IcebergTableWriter` takes a new
`commit_chunk_rows: int = 50_000` constructor param (existing call sites are
unaffected — it's an appended default kwarg). Tradeoff, explicitly accepted:
a large ingestion now produces one Iceberg snapshot per chunk instead of
exactly one; every chunk's `snapshot_properties` still carries the same
`ingestion_id`, so `has_snapshot()`'s BR-9 double-append guard (any snapshot
with a matching id) is unaffected — verified by test, not assumed. A 0-row
staged file still produces exactly one snapshot (the ingestion_id marker),
matching prior behavior — an empty `iter_batches()` would otherwise silently
skip `append()` entirely and lose the double-append guard's marker.

**Test — TDD, bug reproduced before the fix:** added
`test_commit_streams_large_file_in_bounded_chunks` (asserts the exact number
of Iceberg snapshots created for one commit matches `rows / chunk_rows`,
proving genuine chunking rather than a disguised single read) and
`test_commit_empty_ingestion_still_creates_ingestion_id_marker` to
`libs/py-common/tests/test_iceberg.py`, both against the **live** Iceberg REST
catalog + MinIO (already up locally, no restart needed). Stashed the source
fix and ran both new tests first — both failed with
`TypeError: unexpected keyword argument 'commit_chunk_rows'` (the constructor
param didn't exist yet), proving the tests genuinely exercise the new code
path; restored the fix and confirmed both pass. Full `libs/py-common` suite:
36 passed, 0 fails. `dataset-service` suite (the read-side consumer of the
same tables): 233 passed, 0 fails. `ingestion-service` suite (the actual
writer caller, including its own live `test_iceberg_writer_appends_and_reads_back`
integration test): 609 passed, 27 skipped (pre-existing infra-gated skips,
unrelated), 0 fails.

**Found in passing, out of scope for B1:** `ingestion-service/app/container.py`'s
`_build_real()` constructs `IcebergTableWriter(settings.iceberg_catalog_uri,
warehouse=..., s3_endpoint=..., ...)` — `IcebergTableWriter`/`_CatalogHolder`
only ever accepted `cfg`/`catalog`, so this call already raised `TypeError` at
construction before this change too (a pre-existing bug, not introduced here,
and not touched by this fix — flagged separately).

### B5 — bulk `_bulk` reindex + batched reads + `(tenant_id,created_at)` index — DONE

**Root cause confirmed, not assumed:** `case-service/internal/search/projector.go`'s
`Reindex` called `store.AllCaseIDs` (unbounded `SELECT id`), then for every id
did a separate `GetCase` + `CaseCommentText` round trip (2N Postgres queries),
accumulated every resulting `Doc` into one slice (O(N) heap), then wrote it to
OpenSearch with one `IndexDocInto` **PUT per doc** (no `_bulk`). This is the
handler behind `/admin/reindex` (CASE-FR-043), which the stability doctor's
self-heal calls when the search projection needs a full rebuild — so the same
O(N)-in-RAM + 2N-round-trip + per-doc-PUT pattern that breaks at scale also
OOMs the self-heal path. There was also no index supporting the tenant-scoped
`ORDER BY created_at` the old `AllCaseIDs` query relied on (RLS pushes
`tenant_id = current_setting(...)` into the plan as a real equality predicate,
confirmed by reading `000002_rls.up.sql`'s policy — so a `(tenant_id,
created_at)` index is genuinely usable by the planner here, not moot under RLS).

**Fix, three parts, each closing one part of the bottleneck:**
1. **Migration `000007_cases_created_at_idx`** — partial index
   `(tenant_id, created_at, id) WHERE deleted_at IS NULL` on `cases`, matching
   the exact predicate the reindex read (and any future keyset scan) uses.
2. **Batched, paginated Postgres reads** — new `store.PG.CasesPage(tenant,
   afterCreatedAt, afterID, limit)` (keyset pagination on the new index,
   `id` as tiebreaker so ties on `created_at` still page correctly) replaces
   `AllCaseIDs` + N×`GetCase`; new `store.PG.CaseCommentTextBatch(tenant,
   caseIDs)` does the whole page's comment lookup in one round trip via
   `string_agg(... GROUP BY case_id)`, replacing N×`CaseCommentText`.
3. **OpenSearch `_bulk`** — `search.Client.BulkIndexInto(idx, docs)` sends a
   whole page as one NDJSON `_bulk` request instead of one PUT per doc, still
   honoring external versioning by `case_version` (a 409 anywhere in the
   response is discarded per-item, exactly like the old single-doc path; any
   other per-item failure surfaces as an error). `Client.Reindex` (single
   whole-slice call) is replaced by `CreateReindexGeneration` +
   `BulkIndexInto` (called once per page) + `SwapReindexAlias`, so
   `Projector.Reindex` now streams page-by-page instead of building the whole
   rebuilt index in memory first.

Peak memory during reindex is now O(`reindexPageSize`=500 cases) instead of
O(tenant's total case count), and Postgres/OpenSearch round trips drop from
`~2N+N` to `~3×(N/500)`. `GetCase`/`CaseCommentText`/`AllCaseIDs` are
untouched (still used by the single-case incremental projection path,
`ProjectCase`, which never had an N+1 problem).

**Test — TDD, bug reproduced before the fix:** added
`TestReindexBulkPagesLargeTenant` to a new
`case-service/test/integration/reindex_bulk_test.go`, seeding 1247 real cases
(deliberately > 2×500 + a partial page) directly through `store.PG.CreateCases`
against a real ephemeral Postgres 16 (testcontainers, with `000007` applied)
and a real OpenSearch cluster. Stashed the three source files (kept the new
test + migration) and confirmed the build **fails to compile**
(`h.pg.CasesPage undefined`, `h.pg.CaseCommentTextBatch undefined`) — proving
the test genuinely exercises the new methods, not a coincidental pass.
Restored the fix and confirmed: `CasesPage` keyset-paginates every one of the
1247 cases exactly once with no repeats or gaps; `CaseCommentTextBatch`
returns an empty map (not an error) for comment-less cases; the real
`POST /api/v1/admin/reindex` HTTP path (spanning 3 projector pages via
multiple `_bulk` requests) reports `reindexed: 1247`; and the tenant's real
OpenSearch alias holds exactly 1247 docs afterward. Full `case-service` suite
(unit + the Docker-backed integration tier, `CASE_IT=1 go test ./...`,
including the parallel in-flight trigger-handler tests and the existing burst/
acceptance suites): all packages `ok`, 0 fails. `go vet`/`gofmt` clean (no new
formatting debt — the only `gofmt -l` hits in this package are pre-existing
struct-alignment drift in `opensearch.go`'s `Doc` type, untouched by this
change). OpenSearch had to be started for this dev stack (it wasn't running;
confirmed with the user before booting it — a stopped datastore container
only, no application service touched).

### B4 — DuckDB view materialization instead of a full-table copy — DONE

**Root cause confirmed by reading the code, not assumed:** `DuckDB.materialize()`
(`services/query-service/internal/engine/duckdb.go`) ran
`CREATE OR REPLACE TABLE %s AS SELECT * FROM read_parquet(%s)` for every
dataset a query referenced — a physical copy of every row and every column
of the source parquet file(s) into the worker's private catalog, executed
*before* the user's actual SQL ever ran. A chart or case-detail lookup that
only needs 1 of 20 columns, or 50 of 2M rows, still paid for the whole file.
This engine handles "small/interactive execution" (queries route to Trino
above a size threshold), so the blast radius is every chart resolve and every
case-detail read that stays under that threshold — the common path, not the
tail.

**Fix:** changed the one line to `CREATE OR REPLACE VIEW %s AS SELECT * FROM
read_parquet(%s)`. A view is metadata-only — DuckDB's optimizer inlines it
when the user's query runs and pushes that query's own projection and filter
predicates straight into the `read_parquet` scan, so only the columns/row
groups the query actually touches are read. Each worker is single-connection
and recycled after one query (BR-7 isolation), so there is no cross-query
state a view could leak, and nothing here writes to the materialized
relation — a read-only view is a strict improvement with no observed
downside for this access pattern.

**Test — live, against real MinIO/Iceberg data (not just unit tests):**
`go test ./...` (query-service, full suite) green, 0 fails. Ran a temporary
verification probe against a real ingested parquet file in the dev MinIO
(`bronze.<tenant>/ds_<dataset>/data/...parquet`, 20 columns / 48 rows):
confirmed `information_schema.tables.table_type = 'VIEW'` (not `BASE TABLE`);
`EXPLAIN` on `SELECT payer_type, count(*) FROM "main"."claims" GROUP BY 1`
shows the physical `READ_PARQUET` node's `Projections:` lists only
`payer_type` — the one column the query needs, not all 20; the aggregate
result itself (`commercial=19, managed_care=9, medicaid=10,
medicare_advantage=10`, total 48) matches the file's real row count,
proving the view returns identical data to the old table-copy path. Probe
file removed after verification — this is a one-line production fix, not new
permanent test surface.

### B8 — audit-service: batch chain-append + batch ClickHouse insert — DONE

**Root cause confirmed by reading the code, not assumed:** `Consumer.consume()`
(`services/audit-service/internal/ingest/consumer.go`) fetched and processed
exactly one Kafka message per iteration — `FetchMessage` → `Processor.Handle`
→ `CommitMessages` — and `Processor.Handle` called `chain.Manager.Append`
(one distributed-lock acquire/release + ~4 Redis round trips + 1 best-effort
Postgres checkpoint) and `chstore.Store.Insert` (a native ClickHouse batch
statement of exactly one row) per event. audit-service is the platform's
highest-volume consumer (every governed write anywhere emits an audit event),
so this per-event round-trip cost was its throughput ceiling — exactly as B8
flagged.

**Fix, three layers, same three files the audit named:**
1. **`chain.Manager.AppendBatch`** (`internal/chain/chain.go`) — assigns
   positions for many same-tenant events under ONE lock hold: a pipelined
   Redis fast-path check (skip the lock entirely if every item already has an
   assignment — crash-redelivery case), then one `TxPipeline` (atomic
   MULTI/EXEC — stronger than `Append`'s original sequence of separate SET
   calls) writing every event's assignment plus the final seq/head, and one
   Postgres checkpoint upsert for the whole group instead of one per event.
   `Append` itself is unchanged and still used for single-event callers.
2. **`Processor.HandleBatch`** (`internal/ingest/processor.go`) — groups a
   micro-batch's envelopes by tenant, calls `AppendBatch` once per tenant
   group, then one `chstore.InsertBatch` for the whole micro-batch. Shares the
   PII-gate/digest/record-shaping logic with `Handle` via a new `buildPending`
   helper (extracted, not duplicated) so both paths can never drift.
   Per-item terminal errors (bad envelope) surface individually — one bad
   event never blocks or DLQs the rest of the batch.
3. **`Consumer.consume`** (`internal/ingest/consumer.go`) — replaced the
   one-message loop with a bounded micro-batch accumulator (`fetchBatch`):
   blocks for the first message exactly like before (same read-error
   backoff), then opportunistically pulls more for up to `BatchWindow`
   (default 200ms) or until `BatchSize` (default 200) is reached, whichever
   first. `processBatch` decodes+dedups every message (unchanged per-message
   dedup semantics — MASTER-FR-032's crash-window-recovery logic is untouched),
   runs the survivors through `HandleBatch`, and DLQs each terminal item
   individually. A transient error pauses and retries the WHOLE batch without
   committing any offset in it (same BR-6 contract, coarser granularity —
   safe because chain assignment and ClickHouse's ReplacingMergeTree are both
   idempotent under redelivery).

**Test — unit, byte-for-byte correctness anchor:** new
`TestHandleBatchMatchesHandleOneAtATime` runs the same 5 events through
`Handle` one at a time vs. through one `HandleBatch` call and asserts
identical `ChainSeq`/`ChainHash`/`EventID` for every row — batching must be a
pure throughput change, never a behavior change. Plus
`TestHandleBatchGroupsIndependentlyPerTenant` (two tenants in one batch never
share a sequence) and `TestHandleBatchTerminalErrorDoesNotBlockOtherItems`
(one bad envelope doesn't stop the rest). `go test ./...` (audit-service unit
tier): all packages pass.

**Test — live, against real Redis/Postgres/ClickHouse/Kafka:** ran the full
`-tags=integration` suite (real Docker infra, not mocks) before and after —
21 of 24 tests pass identically; the fix didn't regress any of them,
including `TestAC11_TransientClickHouseNoGap` (HIGH-1: a retried event reuses
its assigned seq, no chain gap) and `TestAC05_ChainTamperEvidence` (chain
integrity holds). The other 3 (`TestAC04_DLQEnvelopeInvalid`,
`TestAC15_DLQRedrive`, `TestAC11b_ConcurrentAppendSingleWriter`) fail on
**unmodified `main` too** — verified by `git stash`-ing this change and
re-running them against the identical live infra: same failures, same
symptoms (a ClickHouse `Memory limit (total) exceeded: maximum 1.50 GiB` for
two of them; the third times out draining a `case.events.v1` backlog that
this one shared local dev stack has accumulated over a full day of repeated
test runs — traced with temporary debug logging down to a
consumer/broker-level stall unrelated to any code changed here). Pre-existing
local-environment fragility, not a regression — flagged honestly, not
silently worked around.

### B9/B10 — provision ClickHouse + OpenSearch HA (partial — code done, cloud apply resource-gated) — PARTIAL

**Research before writing anything:** surveyed the actual state of
`deploy/terraform/{aws,gcp,azure,hetzner}/` before designing a fix — grepped
the whole Terraform tree for `opensearch|elasticsearch|clickhouse`: zero real
resources anywhere, on any cloud. Confirmed **no cloud has a native managed
ClickHouse offering** (AWS, GCP, and Azure all lack one — self-hosted-with-
replication is the only honest option everywhere); **Amazon OpenSearch
Service is the ONE native managed OpenSearch/Elasticsearch-family product
across all three clouds** (GCP/Azure have none). This asymmetry drove the
split scope below rather than inventing a fake "managed ClickHouse" resource
that doesn't exist on any provider.

**Done, real, and verified:**
1. **case-service: configurable OpenSearch shard count.** `number_of_shards`
   was a hardcoded `1` in `indexMapping` (`internal/search/opensearch.go`).
   Templated it via a new `search.Options{NumShards, Username, Password}`
   passed to `search.New` (also carries optional HTTP basic auth for Amazon
   OpenSearch Service's fine-grained access control — the codebase's existing
   pattern is username/password everywhere, not request-signing). Verified
   against the **real** local OpenSearch cluster: `TestOpenSearchConfigurableShardCount`
   creates a tenant index with `NumShards: 3` and reads back
   `GET /<alias>/_settings` to confirm `number_of_shards: "3"`, not the old 1.
2. **audit-service: ClickHouse HA-capable config (Keeper-coordinated
   replication, opt-in).** `chstore.Config` gained `Addrs []string` (multiple
   node endpoints; falls back to the existing single `Addr` when empty) and
   `Replicated bool`, which switches `Migrate`'s DDL from
   `ReplacingMergeTree` to `ReplicatedReplacingMergeTree('/clickhouse/tables/
   {shard}/audit_events', '{replica}', ingested_at)` — same columns,
   partitioning, ordering, and the existing 7-year WORM TTL either way (the
   TTL was already present pre-existing; B9's "retention/TTL" ask was already
   satisfied, nothing to add there). New `CLICKHOUSE_ADDRS`/
   `CLICKHOUSE_REPLICATED` env vars in `cmd/server/main.go`, defaulting to the
   unchanged single-node dev/Hetzner path. Pulled the DDL string-building into
   a pure `buildMigrateDDL` function so both engine variants are unit-tested
   without a live cluster (`chstore_test.go`) — a genuinely replicated,
   Keeper-coordinated ClickHouse cluster isn't available in this environment
   to verify end-to-end, so this is deliberately scoped to "config + DDL
   correctness," not a live HA verification.
3. **AWS Terraform: real Amazon OpenSearch Service domain** (`opensearch.tf`)
   — multi-AZ (`zone_awareness_config`), 3 data nodes + 3 dedicated masters by
   default (all counts are variables), EBS gp3, encryption at rest +
   node-to-node, HTTPS-only, fine-grained access control (generated master
   password, mirroring the existing `random_password.db_admin`/`redis_auth`
   pattern — never hardcoded). Endpoint + generated credentials wired into
   `secrets.tf`'s `computed_secrets` (`OPENSEARCH_URL`/`OPENSEARCH_USERNAME`/
   `OPENSEARCH_PASSWORD`) and new `opensearch_endpoint`/
   `opensearch_data_node_count` outputs, following the exact existing
   RDS/ElastiCache/MSK conventions in this module. `values-aws.yaml` gained
   `OPENSEARCH_URL`/`OPENSEARCH_NUMBER_OF_SHARDS` config placeholders (CD
   overrides them from the Terraform outputs, same mechanism already used for
   `ICEBERG_WAREHOUSE`).

**Explicitly NOT done, and why (flagged honestly rather than faked):**
- **No cloud account is available in this environment** — verification for
  the Terraform piece stops at `terraform init -backend=false` +
  `terraform validate` (both pass, and are this module's own documented
  no-credentials verification path per its `versions.tf`/README). `plan`/
  `apply` against real AWS need real credentials; this remains exactly what
  the WS3 checklist already called "resource-gated, not code-gated."
- **GCP/Azure/Hetzner still run case-service's self-hosted single-node
  OpenSearch StatefulSet** (`deploy/k8s/data-tier/search-audit.yaml`,
  `replicas: 1`, no multi-node discovery config) and single-node ClickHouse
  (no Keeper). Turning that into a genuine multi-node/Keeper-coordinated
  StatefulSet is real, substantial k8s work (headless-service DNS for stable
  replica addressing, OpenSearch cluster-manager-node discovery, ClickHouse
  Keeper quorum config) that this environment has **no way to validate** — no
  local `kind`/`k3d`/`minikube` cluster and no `kubectl` context configured
  (`kubectl cluster-info` errors, no current-context). Hand-authoring
  untested Keeper/discovery YAML would risk shipping subtly-broken infra with
  no way to catch it, which is worse than leaving the gap explicit. Tracked
  as the next actionable slice of B9/B10 (needs either a real cluster to
  develop against, or the AWS OpenSearch Service path above extended to
  GCP/Azure via Elastic Cloud or an equivalent supported managed vendor, per
  the original design note's "or a supported managed vendor" fallback).
- **Fine-grained access control credentials aren't wired into the Helm
  chart's case-service pod env yet.** `search.New` already accepts
  `Username`/`Password`, and Terraform already generates + publishes them to
  Secrets Manager, but the chart's `env:` entries are unconditional across
  all 4 cloud overlays and the Deployment template has no `optional`
  secretKeyRef support — adding a required env var referencing a secret key
  that only exists on AWS would break pod startup on every other cloud/dev.
  Verified this isn't yet an issue with `helm lint`/`helm template` against
  every values overlay (`values.yaml`, `-aws`, `-gcp`, `-azure`, `-hetzner`
  all render cleanly, 0 errors) precisely because the credential wiring was
  deliberately left out rather than done unsafely. Documented in
  `values-aws.yaml` as the concrete last-mile follow-up.

**Test:** `case-service` full suite (unit + Docker-backed integration,
`CASE_IT=1 go test ./...`): all packages `ok`, 0 fails, including the new
shard-count test. `audit-service`: `go build`/`go vet` clean; new
`chstore_test.go` (2 tests, pure DDL-string assertions, no live cluster
needed) both pass. `audit-service`'s Docker-backed acceptance suite: 9 of 10
pass; `TestAC05_ChainTamperEvidence` fails intermittently — **confirmed via
`git stash` that this reproduces identically on completely unmodified code**
(ran 5 times total across modified/unmodified, symptoms varied between runs:
sometimes a manifest-hash mismatch, sometimes an extra event count), so it's
a pre-existing environment/isolation issue unrelated to this change, not a
regression — flagged separately rather than silently left for someone else to
rediscover. Terraform: `terraform fmt -check`, `terraform init -backend=false`,
`terraform validate` all pass for the AWS module. Helm: `helm lint` and
`helm template` pass for the default values and all four cloud overlays.

### WS5 — coverage gates, schema-snapshot, event-envelope conformance, 1M-row volume soak — DONE

**1. Per-language coverage thresholds.** Measured actual coverage across the
fleet first rather than guessing a floor: Go (`go test ./... -coverprofile`)
ranges 8.4% (case-service, the low-water mark) to 92% across a 9-service
sample; Python (`uv run --with pytest-cov ... --cov=app`, no
pyproject.toml/uv.lock changes needed for any of the 11 services) ranges 27%
(pack-service, the low-water mark) to 83% across **all 11** services measured;
Node (`@vitest/coverage-v8`, added as a real devDependency to both
bff-graphql and ui-web) measured 75.7%/56.0% lines respectively. Set floors
comfortably below every measured minimum — Go 5%, Python 20%, bff-graphql 40%,
ui-web 30% — real "start low" bars that catch a genuinely untested new
package/service, not today's fleet. Wired into `ci.yml`'s `test-go`
(new `Enforce coverage floor` step), `test-python`/`test-python-libs`
(`--cov-fail-under`), and `test-node` (`pnpm run test:coverage`, still running
`test:integration` afterward for bff-graphql). **Verified the gate actually
enforces, not just reports:** temporarily set an unreachable threshold (99%)
in bff-graphql's config, confirmed the run fails with a clear error, reverted.
Ratchet these up over time; never lower without recording why here.

**2. GraphQL schema-snapshot.** bff-graphql had zero snapshot/diff tooling
(`typeDefs.ts` only ever existed as an in-memory `gql` literal). Added
`schema:snapshot` (`scripts/print-schema.ts`, `graphql`'s `print()`) writing
the SDL to a checked-in `schema.graphql`, and a new
`tests/unit/schema-snapshot.test.ts` that fails if the live schema drifts from
it. **TDD-verified:** appended a bogus type to the checked-in file, confirmed
the test fails with a clear message, reverted.

**3. Event-envelope conformance, shared validator.** Found: only 2 of the ~19
services that emit MASTER-FR-031 envelopes had any conformance test
(case-service's Go `assertConformsToMaster`, agent-runtime's Python
`test_envelope.py`) — both duplicated the same field/actor-type checks ad hoc.
Extracted the shared, single-source-of-truth checks (not the case-service-
specific extras like uuidv7-version or resource_urn-non-empty, which stay
local and are a legitimate superset) into `libs/go-common/event.Validate`
(new, unit-tested: accepts every master actor type, rejects the exact
`actor.type="system"` regression BRD 58 already fixed once, rejects each
missing required field, rejects a nil payload) and
`libs/py-common/datacern_common/events.validate_envelope` (same rules, its
own unit tests). **Real adoption demonstrated, not just written:**
agent-runtime's `test_envelope_matches_go_envelope_fields` now calls the
shared `validate_envelope` against its actual `make_envelope()` output instead
of its old ad hoc field checks — proves the shared validator accepts a real
service's real envelope, not just hand-built fixtures. case-service's own
richer test is intentionally left untouched (it's a superset, not a
duplicate). **Rollout completed.** The remaining 7 Go outbox-owning services
(chart-service, notification-service, query-service, rbac-service,
usage-service, identity-service, tool-plane) and 8 Python services with their
own `envelope.py` copy (ai-gateway, dataset-service, semantic-service,
pipeline-orchestrator, memory-service, eval-service, inference-service,
experiment-service) now each have a unit test that builds a real envelope the
way the service actually constructs it and asserts it passes the shared
validator, following the same pattern as agent-runtime's
`test_envelope_matches_go_envelope_fields`. For the 4 Go services that carry
their own local `Envelope` type (query-service, rbac-service, usage-service,
tool-plane) the tests reuse each service's existing unexported `toMaster`
converter rather than adding a new one; identity-service's test reuses its
existing unexported `toEnvelope`. All 15 services' full test suites pass with
the new tests included; no production envelope-construction code was changed
as part of this rollout.

**Two real conformance bugs surfaced by the new tests, not fixed here**
(fixing them is a behavior change, out of scope for additive test coverage —
tracked as follow-ups):
- **rbac-service DLQ envelopes carry `tenant_id = uuid.Nil`.** `toDLQ`
  (`internal/events/consumers.go`) builds its `consumer.poison` envelope with
  a nil tenant, which fails `event.Validate`'s non-nil `tenant_id`
  requirement — a dead-letter event that would itself be rejected by
  consumption-side validation. **Fixed** — see
  `docs/initiatives/rbac-dlq-envelope-tenant-id.md`: `toDLQ` now threads the
  real `tenant_id` through when the source message decoded (the common case),
  falling back to a new non-nil `PlatformTenant` sentinel only when no tenant
  is recoverable at all (undecodable message). Locked in by two new
  conformance tests in `envelope_conformance_test.go`.
- **tool-plane's `domain.PlatformTenant` sentinel is `uuid.Nil`.** Every
  platform-scoped `tool.events.v1` lifecycle event (tool registered,
  version published, deprecated, retired, killed, unkilled, SLA-breached,
  quarantined) is emitted with this tenant and fails `event.Validate` for the
  same reason — confirmed directly against `gcevent.Validate(toMaster(env))`.
  This is broader than the rbac-service case: it affects every platform-scoped
  tool-catalog lifecycle event tool-plane emits today.

**4. 1M-row volume/load soak (`make soak-volume`).** Targets the two WS4
fixes that were specifically about unbounded memory/row-count: B5 (case-service
reindex) and B1 (Iceberg commit). New `TestVolumeReindexAtScale`
(case-service) seeds real rows via `pgx.CopyFrom` straight into Postgres
through the harness's RLS-bypassing admin pool — deliberately skipping
`CreateCases`'s business rules (BR-13's 10,000-open-case-per-workspace limit,
dedup locking, outbox writes), since this is fixture seeding for the READ path
(B5's reindex), not a re-check of case-creation rules already covered
elsewhere — then calls the real `/admin/reindex` HTTP path and asserts the
OpenSearch alias ends up with the exact row count. New
`test_commit_streams_large_volume_in_bounded_memory` (libs/py-common) stages
and commits a real Iceberg append through the real REST catalog + MinIO, using
`tracemalloc` to assert commit()'s **peak** memory stays under a fixed
ceiling regardless of row count (not a per-row one) — proving the streaming
fix holds at volume, not just in principle.

**Measured at the BRD's literal "1M rows," not extrapolated:**
- B5: seeded 1,000,000 cases in 11.6s, reindexed via `/admin/reindex` in
  1m15.8s (13,187 cases/sec) — `TestVolumeReindexAtScale` passes end to end
  (~90s total including container startup).
- B1: staged + committed 1,000,000 rows; peak memory during `commit()` was
  **25.0MB** (`tracemalloc`), completing in ~2.7s.

CI default is 100k rows (~10s total, keeps the `e2e-live` job fast); the
literal 1M scale runs via `make soak-volume VOLUME_ROWS=1000000` (or
`CASE_VOLUME_ROWS=1000000` / `ICEBERG_VOLUME_ROWS=1000000` directly). New
`deploy/local/soak_volume.sh` + `make soak-volume` target, wired into
`ci.yml`'s `e2e-live` job right after the existing `make soak` (same job
already has real Postgres/Iceberg-REST/MinIO/Docker up; no new application
service is booted — both legs run through `go test`/`pytest`, not against a
deployed stack).

**Test:** full regression across everything touched — `libs/go-common`
(`go test ./...`, incl. new `event` package tests): all packages `ok`.
`libs/py-common` (`pytest tests/`): 50 passed (was 36 before this entry — 13
new `events.py` tests + 1 new volume test). `case-service`
(`CASE_IT=1 go test ./...`, incl. the new volume test at both 100k and literal
1M scale): all packages `ok`. `agent-runtime` (`pytest tests/unit`): 287
passed. `bff-graphql`: `test:coverage` + `test:integration` + `typecheck` +
`lint` all pass. `ui-web`: coverage gate passes (typecheck has one
**pre-existing, unrelated** failure in `src/app/api/auth/login/route.ts` —
confirmed via `git stash` on just this change's 2 ui-web files that it fails
identically without them; not touched, out of scope). `make soak-volume`
verified end to end at the CI default. Cleaned up stray `.coverage` files
(`pytest-cov`'s artifact) generated across every Python service measured
during this work and added `.coverage` to `.gitignore` so it doesn't recur.

_See BRD 59 for feature expansion (5B)._

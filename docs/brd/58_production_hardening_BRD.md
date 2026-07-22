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
- [ ] DB/role bootstrap job · [ ] managed OpenSearch/ClickHouse module · [ ] HPA values · [ ] **apply on AWS + prove rollout** (needs a cloud account — resource-gated, not code-gated).

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
  · [x] **B5** bulk reindex + `(tenant_id,created_at)` index — see log below.
- [ ] B9/B10 (=WS3).

---

## WS5 — Test & release confidence

### Analysis / Design
No coverage gates in any language; no contract testing; live-e2e is real but the
default runner flakes. Add: per-language coverage thresholds (start low, ratchet);
GraphQL schema-snapshot + event-envelope conformance as CI gates; a load/soak target
(`make soak` exists for restart; add a volume load test at 1M rows for WS4 items).

### Implement / Test
- [ ] coverage thresholds · [ ] schema-snapshot gate · [ ] 1M-row load test harness.

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

_See BRD 59 for feature expansion (5B)._

# Scalability bottleneck audit (millions of records / cases per tenant)

**Status:** analysis + fix roadmap — 2026-07-21 · implementation pending
**Related:** [stability-durability](stability-durability.md), memory `project_windrose_stability_doctor`

---

## 1. Analysis

### 1a. Platform / product
A multi-tenant SaaS must hold millions of records/cases per tenant without OOMs,
runaway queries, or silent disk-fill. Today the **interactive read path is solid
to ~100k**, but three hard areas break before millions, and two stateful stores
have no production deployment at all. This gates any customer install past a pilot.

### 1b. Technical
Read-only audit; evidence by `file:line`. Severity: BLOCKER (~1M) / RISK (~10M).

**BLOCKERs**
- **B1 — Iceberg commit re-materializes the whole dataset in memory.** `libs/py-common/windrose_common/iceberg.py:108,200-207` — `commit()` reads the staged parquet back as one `pa.Table`, then `.cast()` copies it again; all bronze cols are `large_string`. Ingestion *streams* through decode/stage but OOMs at commit; `max_running_per_tenant=5` lets 5 stack. Code.
- **B2 — No total upload size/row cap.** `ingestion-service/app/config.py:67-71`, `uploads.py:323` — only per-part (≤64 MiB) + active-count (20); `bytes_total` is advisory. Feeds B1. Config.
- **B3 — Query-service applies the caller's LIMIT only for agent callers.** `query-service/internal/exec/plan.go:232-242` — service callers (charts) run `ExecSQL` unmodified; bounded only by `MaxResultRows=5M`/`MaxResultBytes=1GB`. A chart matching 2M rows executes fully to show 5000. Code.
- **B4 — DuckDB adapter full-copies parquet per execution.** `query-service/internal/engine/duckdb.go:227` — `CREATE TABLE AS SELECT * FROM read_parquet(...)`, no projection/pushdown; even a 1-row case-detail lookup copies the dataset. Capped by router's ≤5GB DuckDB threshold (then Trino). Code.
- **B5 — Full-tenant case reindex is O(N) in RAM + 2N round-trips + per-doc PUT.** `case-service/internal/search/projector.go:53-74`, `store/pg_sla.go:203-222` — `AllCaseIDs` unbounded, then per-id GetCase+CommentText, all docs in a slice, one PUT each (no `_bulk`), no `(tenant_id,created_at)` index. **This is the `/admin/reindex` the stability doctor/reconcile relies on** → self-heal OOMs at ~1M cases. Code.
- **B6 — Transactional outbox never pruned.** `libs/go-common/outbox/relay.go:93-97`, `libs/py-common/windrose_common/outbox.py:75-81` — only `MarkPublished`; no `DELETE` across 20+ outbox tables. Relay query stays fast (partial index) but heap/TOAST grow forever. Infra.
- **B7 — `processed_events` dedup table: no TTL, no `created_at` index.** `dataset-service/migrations/.../0001_initial.py:175`, insert `memory-service/app/store/sql.py:712` — one row per event forever across ~7 services. Code.
- **B8 — Audit ingest single-row insert + per-tenant serialized lock.** `audit-service/internal/chstore/chstore.go:111` (`Insert`→`InsertBatch([]{r})`), `chain/chain.go:77` (per-tenant/date lock). Highest-volume consumer → throughput ceiling. Code.
- **B9 — ClickHouse (audit WORM) has no prod deployment.** Not in any terraform (`terraform.tfvars.example`: `CLICKHOUSE_URL="" # fill in later`); only dev StatefulSet `deploy/k8s/data-tier/search-audit.yaml:97` (replicas 1, ~1.5GiB, no Keeper/replica). Infra.
- **B10 — OpenSearch (case search) no prod deployment; shards hardcoded to 1.** `search-audit.yaml:15` single-node; `case-service/internal/search/opensearch.go:75` `number_of_shards:1` as a const. Infra.

**RISK-tier:** LIMIT/OFFSET browse with global `row_number()` window (`dataset-service/app/adapters/duckdb_browse.py:50`); full-parquet→pandas in entity-resolution + profiler; two unpaginated growth endpoints (eval `runs/{id}/cases`, inference `/lineage`); agent decision-effectiveness groups whole tenant history in Python; all Kafka consumers one-message-at-a-time; `case_events` `PARTITION BY RANGE` with only a DEFAULT partition; no retention on transcripts/proposals/notifications; HPA template exists but no `values.yaml` sets `autoscale` (dead); 14 Python services at `replicas:1`; Iceberg catalog on single-node sqlite.

**What's already good:** streaming decode/stage for every format; genuine engine pushdown for row-browse + chart GROUP BY; clean Trino adapter; streaming result store with keyset cursors + mid-query ceilings; case-service keyset (`search_after`) pagination, ≤200 caps, bounded bulk/export; Redis dedup 24h TTL group-namespaced; usage-service retention reaper (the template to copy); bff DataLoaders + cursor pagination; cloud terraform relational/cache/streaming/object tier is HA by default (RDS multi-AZ, MSK 3-broker, ElastiCache failover, S3 versioned/KMS).

---

## 2. Architecture & Design (fix roadmap)

Priority order (highest value / lowest risk first):

1. **B1+B2 — streaming Iceberg commit + hard size cap.** Append via `iter_batches` / incremental `Table.append` instead of one full read; enforce a server-side max rows/bytes at upload assembly. *The true ingest ceiling — nothing else matters if data can't load.*
2. **B6+B7 — retention reapers.** Copy usage-service `EnforceRetention` pattern: prune published outbox rows past a grace window; TTL `processed_events` (+ `created_at` index). Cheap, closes the unbounded-growth class.
3. **B3 — wrap `ExecSQL` with the caller's LIMIT for all callers.** Small, isolated; big waste reduction.
4. **B9+B10 — provision ClickHouse + OpenSearch** in Helm/Terraform: persistent + replicated, configurable shards/replicas, retention/TTL. Required before real scale.
5. **B5 — bulk `_bulk` reindex + batched reads + `(tenant_id,created_at)` index.** Needed for scale *and* the stability self-heal.

---

## 3. Implementation & Test
Not started — this is the analysis + roadmap. Each item to be taken through
analyze→design→implement→test as its own initiative doc. Verification for each
must include a load test at the target row count, not just unit tests.

**Verdict:** cannot handle millions/tenant today; can to ~100k. Items 1–5 move it
to "scales to millions." Read path is largely production-grade already.

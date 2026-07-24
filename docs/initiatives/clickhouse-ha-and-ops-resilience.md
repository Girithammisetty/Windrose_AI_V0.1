# ClickHouse HA wiring + stateless service horizontal scaling + ops resilience proof

**Status:** in-progress — 2026-07-23
**Commits:** (uncommitted at authoring time — see file list in §3)  ·  **Related:** B9 (scalability audit, `docs/initiatives/scalability-audit.md`), `docs/initiatives/stability-durability.md`, `services/audit-service/internal/chstore/chstore.go`

---

## 1. Analysis

### 1a. Platform / product
audit-service is Datacern's append-only WORM (write-once-read-many) store for
every governed decision, proposal, and four-eyes approval on the platform —
it is the thing a regulator or an internal auditor points at. Today it has a
single point of failure: one ClickHouse pod. If that pod's disk fails or the
node it's on is lost, the platform loses (or can't write) its audit trail
until it's manually recovered — a compliance-relevant outage, not just an
availability one. Separately, several of the platform's stateless Python
services have sat at `replicas: 1` since they were first deployed, which caps
throughput and means a single pod restart is a visible blip rather than a
non-event.

### 1b. Technical
Confirmed by a prior research pass (not re-verified here, per instruction):

- `deploy/k8s/data-tier/search-audit.yaml:97-143` — the only k8s manifest for
  ClickHouse — is a single-replica StatefulSet: no Keeper, no `{shard}/{replica}`
  macros, a plain (non-headless) `ClusterIP` Service. It is applied directly
  with `kubectl`, outside this Helm chart. There is no managed ClickHouse
  product on AWS/GCP/Azure, so there is no Terraform HA story for it either —
  a genuine infra gap, not an oversight.
- The Go app layer already fully supports HA and is unit-tested:
  `services/audit-service/internal/chstore/chstore.go`'s `Config.Addrs
  []string` / `Config.Replicated bool` and `buildMigrateDDL` (renders
  `ReplicatedReplacingMergeTree('/clickhouse/tables/{shard}/audit_events',
  '{replica}', ingested_at)` when `Replicated` is true).
  `services/audit-service/cmd/server/main.go:134-143` reads
  `CLICKHOUSE_ADDR` / `CLICKHOUSE_ADDRS` (comma-separated) /
  `CLICKHOUSE_REPLICATED` from the environment. None of the three HA-shaped
  env vars were wired into Helm — only `CLICKHOUSE_ADDR` /
  `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` appeared in
  `deploy/helm/datacern/values.yaml` (pre-change lines 851-856). The app
  could run HA; nothing could turn it on.
- 9 Python services sat at `replicas: 1` in `values.yaml`: ingestion-service,
  dataset-service, agent-runtime, memory-service, pipeline-orchestrator,
  experiment-service, semantic-service, eval-service, pack-service.
- No self-managed, quorum-coordinated StatefulSet pattern exists anywhere in
  this repo to copy — every stateful store (Postgres, Redis, Redpanda,
  OpenSearch, ClickHouse) is single-replica in k8s; the platform's real HA
  story elsewhere is cloud-managed services (RDS multi-AZ, ElastiCache
  replication groups, MSK, managed OpenSearch). This work establishes a new
  pattern rather than extending an existing one.

---

## 2. Architecture & Design

**Wiring the existing HA capability through Helm (safe, additive).**
`values.yaml` gained a `clickhouse:` section with `addrs: []` and
`replicated: false` (both empty/false by default), plus `ha.enabled: false`
gating new infra. `templates/deployment.yaml`'s per-service env block gained
an `audit-service`-only conditional that appends `CLICKHOUSE_ADDRS` (only if
`clickhouse.addrs` is non-empty) and `CLICKHOUSE_REPLICATED` (only if
`clickhouse.replicated` is true) after the existing
`CLICKHOUSE_ADDR`/`USER`/`PASSWORD` secretKeyRefs. Because both new values
default empty/false, a default `helm template` render is byte-identical to
before on every line except the 5 intentional replica bumps (§3, verified by
diff). This was deliberately done in the template (not by trying to
template values.yaml itself, which Helm does not render) — the smallest
surface change that makes the two existing Go-side env vars reachable.

**New: ClickHouse Keeper + multi-replica ClickHouse, gated `clickhouse.ha.enabled`.**
Two new templates, both no-ops unless the flag is set:

- `templates/clickhouse-keeper.yaml` — a 3-node (default; `clickhouse.ha.keeper.replicas`)
  ClickHouse Keeper StatefulSet + headless Service (`clickhouse-keeper-headless`).
  Every pod needs a unique numeric `server_id` consistent with a
  raft_configuration every pod shares — since a StatefulSet's pod template
  must be identical across replicas, an `initContainer` derives
  `server_id = ordinal + 1` from `$HOSTNAME` at boot (ordinals are the
  stable, predictable `<name>-N` suffix) and writes it as a `config.d`
  override; the common raft peer list (all N hostnames) is pre-rendered once
  via Helm `range` into the shared ConfigMap.
- `templates/clickhouse-ha.yaml` — a `clickhouse.ha.replicas`-node (default 3)
  ClickHouse StatefulSet, a headless Service (`clickhouse-ha-headless`, for
  stable per-pod DNS Keeper and `CLICKHOUSE_ADDRS` both need) and an
  additional load-balanced ClusterIP (`clickhouse-ha`) for ad hoc client use.
  One logical shard ("01"), N replicas — matches chstore.go's
  `{shard}/{replica}` path, which only ever needs one shard for
  audit-service's write volume. Per-pod `<macros><replica>` values are
  pre-rendered for every ordinal into one ConfigMap
  (`clickhouse-ha-macros`, keys `macros-0.xml` … `macros-(N-1).xml`) via
  Helm `range` over `clickhouse.ha.replicas`, and a startup `initContainer`
  (same trick as Keeper) copies its own ordinal's file into
  `config.d/macros.xml` by parsing `$HOSTNAME`. A shared ConfigMap carries
  the common `<zookeeper>` block (pointing at every Keeper pod's headless
  DNS name) and `<listen_host>`/memory-limit settings identical to the
  existing single-node k8s manifest.
- **Neither template touches, removes, or renames anything in
  `deploy/k8s/data-tier/search-audit.yaml`** (which isn't part of this Helm
  chart at all — it's applied by plain `kubectl` and stays exactly as-is)
  nor collides with it: all new chart-managed objects use the distinct
  `clickhouse-ha*` / `clickhouse-keeper*` names, verified with no duplicate
  `(kind, name)` pairs across the full render (§3).
- **Explicitly not done / not claimed:** this was authored and validated with
  `helm lint` / `helm template` only. There is no multi-node k8s cluster in
  this environment to `helm install` it against, so raft quorum formation,
  actual replica convergence, and failover have not been observed running —
  only the manifests' shape and Helm's own templating have been verified.
  Before pointing a real audit-service at this, prove out the Keeper quorum
  against a throwaway cluster (kind/k3d) first.

**Stateless Python service replica bumps.** See §3 for the per-service
research and verdicts; the rule applied was: bump only if the service's
background loop(s) use `FOR UPDATE SKIP LOCKED` (or an equivalent
partition-safe mechanism like a Kafka consumer group) — anything doing a
plain `SELECT` on a timer and then acting per-row/per-tenant is unsafe to
duplicate across replicas.

---

## 3. Implementation & Test

### Files changed / created
- `deploy/helm/datacern/values.yaml` — added `clickhouse:` section (`addrs`,
  `replicated`, `ha.enabled`/`replicas`/`image`/storage/resources,
  `ha.keeper.*`); bumped `replicas: 1 → 2` for `dataset-service`,
  `memory-service`, `semantic-service`, `eval-service`, `pack-service`;
  added an explanatory "NOT bumped" comment on `ingestion-service`,
  `agent-runtime`, `pipeline-orchestrator`, `experiment-service`.
- `deploy/helm/datacern/templates/deployment.yaml` — `audit-service`-only
  conditional env additions for `CLICKHOUSE_ADDRS` / `CLICKHOUSE_REPLICATED`.
- `deploy/helm/datacern/templates/clickhouse-keeper.yaml` (new) — Keeper
  StatefulSet + headless Service + config, gated `clickhouse.ha.enabled`.
- `deploy/helm/datacern/templates/clickhouse-ha.yaml` (new) — HA ClickHouse
  StatefulSet + headless/ClusterIP Services + per-ordinal macros ConfigMap,
  gated `clickhouse.ha.enabled`.
- `docs/initiatives/clickhouse-ha-and-ops-resilience.md` (this file).

### Replica-bump decisions (per-service research, each entrypoint read directly)

| Service | Verdict | Reason |
|---|---|---|
| dataset-service | **Bumped → 2** | Only background loops are relay/retention via `OutboxDispatcher.run_once()` (`FOR UPDATE SKIP LOCKED`) — safe under N replicas. |
| memory-service | **Bumped → 2** | Relay/retention loops safe (`FOR UPDATE SKIP LOCKED`); consume loop is a Kafka consumer group (partitions across replicas). |
| semantic-service | **Bumped → 2** | Only loop is `relay_loop`, safe (`FOR UPDATE SKIP LOCKED`). |
| eval-service | **Bumped → 2** | Only loop is `_relay_loop`, safe (`FOR UPDATE SKIP LOCKED`). |
| pack-service | **Bumped → 2** | No background loop in `app/main.py` at all — pure request/response API. |
| ingestion-service | **Left at 1** (commented) | `_outbox_relay_loop` claims rows via `ing_outbox_claim_pending`, a plain `SELECT` with no `FOR UPDATE SKIP LOCKED` (unlike every other service's dispatcher) — 2 replicas would double-publish e.g. `ingestion.completed` before either marks a batch published. |
| agent-runtime | **Left at 1** (commented) | `RetrainScheduler.run()` polls `list_due_retrain_watches()` (plain `SELECT`, no locking) — 2 replicas would both see the same due watch and both open a duplicate `mlops.open_retrain` proposal. |
| pipeline-orchestrator | **Left at 1** (commented) | `scheduler_loop` → `SqlScheduleScanner.due()` (plain `SELECT`) and `_fire_one()` creates the run *before* advancing `next_fire_at` — 2 replicas racing the same tick would double-fire the same recurring pipeline. |
| experiment-service | **Left at 1** (commented) | `reconcile_loop`/`expiry_loop`/`inbox_loop` each do a plain `SELECT DISTINCT` over tenants with no per-tenant locking — 2 replicas would run duplicate reconciliation/expiry/inbox-application concurrently. |

(dataset-service, semantic-service, eval-service, pack-service matched the
task's own prior guess; memory-service was additionally confirmed safe by
reading its loops directly rather than assumed.)

### Helm validation

`helm lint deploy/helm/datacern` (both before and after all changes):
```
==> Linting deploy/helm/datacern
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```
`helm lint --strict` after all changes: same result, 0 failed.

**Default-values render, before vs. after — full diff:**
```
$ diff template_default_before.yaml template_default_after.yaml
1067c1067 / 1450c1450 / 2596c2596 / 3191c3191 / 3312c3312
<   replicas: 1
---
>   replicas: 2
```
Exactly the 5 intentional replica bumps (dataset-service, memory-service,
semantic-service, eval-service, pack-service) — zero other lines changed.
Line count identical (4767 → 4767). This proves the ClickHouse HA templates
and the new `CLICKHOUSE_ADDRS`/`CLICKHOUSE_REPLICATED` env wiring are
completely inert at default values — today's single-node dev/prod behavior
is unchanged.

**`helm template deploy/helm/datacern --set clickhouse.ha.enabled=true --set observability.prometheusRule.enabled=true`:**
renders cleanly (`exit=0`, no stderr), 5275 lines (vs. 4767 default — the
delta is the new Keeper StatefulSet+Service+ConfigMap, the new ClickHouse-HA
StatefulSet+2 Services+2 ConfigMaps, and the PrometheusRule bundle). Verified:
- No duplicate `(kind, name)` pairs anywhere in the combined render (checked
  programmatically over the full output).
- `audit-service`'s Deployment renders `CLICKHOUSE_ADDR/USER/PASSWORD`
  (unchanged, existing secretKeyRefs) plus, when
  `--set clickhouse.replicated=true --set clickhouse.addrs={...}` is also
  passed, `CLICKHOUSE_ADDRS` (comma-joined literal `value:`) and
  `CLICKHOUSE_REPLICATED: "true"` immediately after them — confirmed by
  direct inspection of the rendered Deployment YAML.
- `clickhouse-ha` StatefulSet: 3 replicas, headless + ClusterIP Services,
  per-ordinal `macros-N.xml` ConfigMap (3 entries rendered for `macros-0/1/2.xml`,
  each with the correct `<replica>clickhouse-ha-N</replica>`), initContainer
  selecting its own file by `$HOSTNAME` ordinal.
- `clickhouse-keeper` StatefulSet: 3 replicas, headless Service, raft
  configuration listing all 3 peers by their headless DNS names, initContainer
  deriving `server_id` from ordinal.

### Live `make doctor` / `make soak` (this session, current running dev stack)

`make doctor` (no heal) found one **real, pre-existing** issue unrelated to
this session's Helm/replica changes (I did not touch case-service,
reconcile scripts, or docker-compose.dev.yml): tenant
`019f90e7-c5b6-7dea-b842-0c2207e6a0e3` had no OpenSearch `cases-<tenant>`
index (`✗ case index MISSING ... — Cases page will 503`), 1 problem
reported, exit 1.

`make doctor HEAL=1` ran `reconcile.sh` (rbac projections, all 4 tenants OK)
and `reconcile_cases.sh` (case projections; the affected tenant reindexed 0
documents — it's an empty tenant with zero cases). The command's own summary
still reported "1 problem(s)" because `doctor.sh` computes its fail count in
the check pass *before* running the heal pass and does not re-verify
afterward (a minor UX quirk in the script worth knowing, not fixed here since
it wasn't in scope for this task — the heal itself did work, confirmed next).

`make soak` (full live run): baseline `./doctor.sh` was GREEN — this time the
`019f90e7...` case index showed present (the heal from the prior step had
taken effect; likely index-creation/refresh lands slightly after the
`reindexed=0` response, which is why the immediately-following `HEAL=1`
summary still looked red). Soak then:
1. Restarted every stateful container (`postgres redis opensearch clickhouse
   redpanda minio iceberg-rest`) — volumes preserved, not wiped.
2. Waited for all 7 to report healthy — all did within timeout.
3. Re-ran `doctor.sh` — GREEN again: all 7 named volumes present, 4 active
   tenants, all 4 rbac projections present, all 4 case indices present.

Final line: **`SOAK PASS — platform survived an infra restart, still
GREEN.`** No durability regression found in this session's live run. The
one real finding (an empty tenant's OpenSearch case index not existing until
after an explicit heal) is a genuine, narrow edge case in
`reconcile_cases`/case-service's index-bootstrap path for zero-case tenants
— outside this task's ownership (case-service business logic), flagged here
for a follow-up rather than fixed.

### Known limits / honesty notes
- ClickHouse Keeper + HA ClickHouse: **authored and `helm lint`/`helm
  template`-validated only**. No live k8s cluster was available to `helm
  install` this against — quorum formation, replica convergence, and actual
  failover behavior are unverified. Treat as reviewed IaC, not a proven HA
  deployment.
- 4 of the 9 Python services remain intentionally at `replicas: 1` because
  their in-process schedulers/reconcilers are not yet safe to duplicate
  (see table above); each has an inline comment explaining exactly what would
  need to change (row-level locking or leader election) before it can scale.
- The zero-case-tenant OpenSearch index gap surfaced by `make doctor` this
  session is real and reproducible right now on the live stack; it is not
  caused by anything in this change set.

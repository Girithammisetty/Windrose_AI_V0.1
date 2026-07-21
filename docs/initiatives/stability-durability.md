# Stability: durability & self-heal

**Status:** implemented — 2026-07-21 · live-green pending an infra recreate
**Commits:** `cf78f3c` (case projection), `d3f1da5` (volumes + doctor), `108bbd3` (soak + CI gate)
**Related:** memory `project_windrose_stability_doctor`, `project_windrose_stability_reconcile`

---

## 1. Analysis

### 1a. Platform / product
The platform kept breaking after restarts (403-everywhere, "Iceberg wiped", the
Cases page 503). Each felt like a new bug; each eroded trust. For a product sold
on governance, "loses data / breaks on reboot" is disqualifying. Goal: make the
stack durable by default and self-diagnosing, so the failure surfaces in seconds
(a command) instead of via a broken page.

### 1b. Technical
All incidents share one shape: **derived/append state on a store that is either
not durable (no volume) or not rebuilt on boot from the Postgres source of truth.**
- Audited every stateful service in `deploy/docker-compose.dev.yml`. Missing named
  volumes: **clickhouse** (`/var/lib/clickhouse` — the 7-yr WORM audit trail, silently
  lost on recreate) and **redis** (derived rbac projection + dedup). `docker compose
  down` never passes `-v`, so a container recreate wiped them.
- Non-gaps (stateless; data in durable Postgres/MinIO): temporal, mlflow, keycloak, trino, `*-init`.
- Case search: OpenSearch had no volume (fixed) + the search consumer only projects
  *new* events, so a lost index was never backfilled → `case-service` 503
  "search projection unavailable" (`internal/domain/errors.go:81`).

---

## 2. Architecture & Design
Two axes, matching the two failure causes:
- **Durability:** every stateful store gets a named volume; redis runs `--appendonly yes`.
- **Boot self-heal:** derived projections rebuild from Postgres on boot. `up.sh` runs
  `reconcile.sh` (rbac Redis projection) + `reconcile_cases.sh` (OpenSearch case index,
  every boot — even `--platform-only`, so the index always exists and search returns
  `[]` not 503). rbac also has a runtime Redis-miss→SQL fallback (RBC-FR-045).
- **Detector — `make doctor`:** per active tenant, check (1) every named volume exists,
  (2) rbac projection present, (3) case index present → GREEN/RED + one-line remedy;
  `HEAL=1` runs the reconciles. Surfaces the class before a user hits it.
- **Contract — `make soak`:** baseline doctor GREEN → restart stateful containers
  (volumes preserved) → doctor must STILL be GREEN. Any regression turns it RED.
- **CI gate:** `make doctor` post-`up` in `e2e-live` (fail fast), `make soak` after the
  e2e suite (clean env).

Invariant: reconciles are idempotent and rebuild from the source of truth, never fabricate.

---

## 3. Implementation & Test
- `deploy/docker-compose.dev.yml`: `clickhousedata`, `redisdata` volumes (+ redis AOF);
  `opensearchdata` earlier. (Parallel session added `redpandadata` durability comment.)
- `deploy/local/reconcile_cases.py|.sh`, wired into `up.sh` (every boot).
- `deploy/local/doctor.sh` + `make doctor`; `deploy/local/soak.sh` + `make soak`.
- `.github/workflows/ci.yml` `e2e-live`: doctor gate + soak step.

**Verified:** doctor runs clean live (rbac + case projections GREEN, both tenants);
`reconcile_cases` reindexed both tenants (2 ok, 0 failed); `GET /cases` → 200;
compose config valid; soak correctly *refuses* on the current non-durable baseline
(guard has teeth); CI YAML valid, steps ordered.

**Deferred / honest gaps:** volume fixes apply only on **container recreate**
(`make down ARGS=--infra && make up`) — the running stack is still non-durable until
then, and doctor says so. Production durability (managed stores, backup/DR) is a
separate track. The reindex self-heal is O(N)-in-RAM (see
[scalability-audit](scalability-audit.md) B5) → OOMs at ~1M cases.

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
- [x] **B2** upload size/row cap · [x] **B7** `processed_events` retention + index — see log below.
- [ ] B1 streaming commit · [ ] B6 outbox reaper · [ ] B3 LIMIT-all-callers · [ ] B5 bulk reindex · [ ] B9/B10 (=WS3).

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
_Appended as increments land. See BRD 59 for feature expansion (5B)._

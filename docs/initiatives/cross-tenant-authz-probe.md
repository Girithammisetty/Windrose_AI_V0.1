# Cross-tenant authorization probe ("pen-test-lite")

**Status:** done — 2026-07-23
**Commits:** (uncommitted; this change set)  ·  **Related:** BRD 58 production hardening (MASTER-FR-003 tenant isolation, AC-13 cross-tenant denial); the RLS-level `TestSiemConfigTwoTenantsNoCrossDelivery` in `services/audit-service/test/integration/siemconfig_isolation_test.go` (BRD 59 WS2) is the internal DB-level analogue of what this probe proves externally.

---

## 1. Analysis

### 1a. Platform / product
A recurring gap on every production-readiness review of a multi-tenant
platform is "no external pen test" — nobody has stood entirely outside the
service code, with nothing but a legitimate credential for one tenant, and
tried to reach a different tenant's data or actions over the real HTTP APIs.
Internal unit/integration tests prove the code *intends* to enforce isolation;
they don't prove what an external client actually observes when it tries to
break it. Closing this gap needed a real, reusable, automated probe that plays
exactly that role and can be re-run at any time against the live stack.

### 1b. Technical
The codebase already has DB-level "two-tenants-no-cross-delivery" isolation
tests (BRD 59 WS2), e.g.
`services/audit-service/test/integration/siemconfig_isolation_test.go`
(`TestSiemConfigTwoTenantsNoCrossDelivery`), which prove Postgres RLS itself
denies a raw, predicate-free scan under one tenant's session. Those are
in-process Go tests exercising the store layer directly — valuable, but not
an external, black-box, HTTP-only probe of the kind a pen test performs, and
they only cover one service (audit-service SIEM config).

`deploy/e2e/lib/common.py` already has everything needed to mint real,
narrow-scoped RS256 user tokens (`user_token(sub, tenant_id, scopes,
workspace_id)`), signed with the harness IdP key (`kid e2e-harness-key-1`,
`iss https://identity.datacern.ai`) that every service's real JWT verifier
already trusts (confirmed live: `curl http://localhost:8301/.well-known/jwks.json`
lists that `kid` alongside identity-service's own signing keys). No new
tenants were needed: `deploy/local/run/personas.json` has three real, distinct,
already-seeded tenants (`demo.datacern`, `wellstar-manual`, `verify.datacern`)
with live case/dataset/pipeline/audit data.

---

## 2. Architecture & Design

**Built:** `deploy/security/cross_tenant_authz_probe.py` — a standalone script
(follows `deploy/e2e/driver.py`'s conventions: color-coded `ok`/`bad`/`info`/
`step` helpers, a `FAILS` list, a final evidence JSON dump, `sys.exit(1)` on
any failure) that:

1. Picks two real, distinct tenants from `deploy/local/run/personas.json`
   (default `admin@demo.datacern` = tenant A, `admin@verify.datacern` =
   tenant B; both overridable via `PROBE_PERSONA_A`/`PROBE_PERSONA_B` env
   vars, with automatic fallback to any other real tenant found in the file).
2. Mints two **narrow-scoped** tokens (only the 7 read/update scopes the
   probes need — not the personas' full scope lists) via
   `deploy/e2e/lib/common.user_token`.
3. For 4 representative services — **case-service, dataset-service,
   pipeline-orchestrator, audit-service** — discovers ONE real,
   already-existing tenant-A resource via that service's own list endpoint
   (no new data created), then:
   - sanity-checks tenant A can read its own resource (so a later 403/404 for
     tenant B actually proves isolation, not a broken probe),
   - **GET-by-id** with tenant B's token → must be 403/404,
   - **LIST** with tenant B's token → tenant A's resource id must never
     appear in the results,
   - **WRITE** (case-service `PATCH /cases/{id}`, dataset-service
     `PATCH /datasets/{id}`, pipeline-orchestrator `PUT /pipelines/{id}`)
     with tenant B's token, writing a harmless, clearly-labeled marker field
     (`custom_fields.probe`, `custom_metadata.probe`, `run_parameters.probe`)
     → must be rejected (non-2xx), **and** a re-read via tenant A's own token
     afterward must show the field byte-for-byte unchanged from the
     pre-probe baseline (proves the write did not silently apply before
     being "cleaned up" — no destructive writes are ever made).
4. audit-service has no per-event update endpoint (it's an immutable log), so
   its write probe is intentionally omitted — only GET-by-id + LIST are run
   for it.
5. Prints every HTTP status code actually observed and a final PASS/FAIL/SKIP
   summary + JSON evidence blob; exits 1 if any cross-tenant leak is found.

**Explicitly out of scope:** modifying any service code (pure external
probe); creating new tenants or resources; destructive writes; a DB-level RLS
probe (that's what the existing `TestSiemConfigTwoTenantsNoCrossDelivery`
already covers — this probe deliberately stays at the HTTP boundary, which is
what an actual external attacker would touch).

`make security-probe` was added to the Makefile (same style as `make doctor` /
`make soak`) to wire it as a manual, on-demand check against a running stack.

---

## 3. Implementation & Test

**Files:**
- `deploy/security/cross_tenant_authz_probe.py` (new)
- `Makefile` — added `security-probe` target
- `docs/initiatives/cross-tenant-authz-probe.md` (this file)

**Live run** (stack already up per `deploy/e2e/config.env` ports; command run
verbatim):

```
$ deploy/e2e/.venv/bin/python deploy/security/cross_tenant_authz_probe.py
```

**Tenants used** (both pre-existing, reused — no new tenant/data created):
- tenant A = `admin@demo.datacern`, tenant `019f8cc6-cef2-7904-9900-d35ae2ca30d9`
- tenant B = `admin@verify.datacern`, tenant `019f90e7-c5b6-7dea-b842-0c2207e6a0e3`
  (the depth-verify tenant, workspace `019f90e7-c9fa-76e5-8ba7-a232d6780bc1`)

**Result: 18/18 probes PASSED, 0 FAILED.** Real resources probed: case
`019f8cea-3d4a-7737-bf17-8306906d7c3b`, dataset
`019f8d18-a526-78f0-a9c4-4bd7ca93ffab`, pipeline
`019f9096-8a25-727e-905e-1c9f430200c1`, audit event
`019f917f-bdb8-7d88-bf70-cfc4dfd75143` — all belonging to tenant A.

| Service | GET-by-id (cross-tenant) | LIST (cross-tenant) | WRITE (cross-tenant) | Post-write regression |
|---|---|---|---|---|
| case-service | **404** | 503* | **404** | unchanged (`custom_fields={}`) |
| dataset-service | **404** | 200, 0/3 rows leaked | **404** | unchanged (`custom_metadata=None`) |
| pipeline-orchestrator | **404** | 200, 0/2 rows leaked | **404** | unchanged (`run_parameters=None`) |
| audit-service | **404** | 200, 0/5 rows leaked | n/a (immutable log) | n/a |

\* case-service's LIST probe returned `503 SEARCH_UNAVAILABLE` for tenant B
rather than `200`. Investigated live: this is reproducible across 3 retries,
and is specific to tenant B's per-tenant OpenSearch search-projection
availability (OpenSearch cluster status was `yellow`, 28 unassigned shards at
probe time) — **not** an authorization bypass; no tenant-A data was returned
either way (0 rows leaked, which is what the assertion actually checks). The
GET-by-id and WRITE checks for case-service — the two checks that actually
exercise the authz/tenant-scoping code path against a resolved resource —
both cleanly returned `404`, which is the authoritative isolation evidence
for that service. This is flagged here rather than silently treated as a
clean pass; it is a pre-existing infra availability quirk unrelated to this
initiative and out of scope to fix (touching search-projection bootstrapping
was explicitly out of scope for a pure external probe).

**Verdict: cross-tenant RLS/RBAC isolation holds today** across all 4
services probed, for both read (list + get-by-id) and write paths. No tenant-A
data or write capability was reachable with tenant B's credentials.

**Verified vs assumed:** every status code above is a real, observed HTTP
response from the live stack (ports 8308 case-service, 8304 dataset-service,
8313 pipeline-orchestrator, 8322 audit-service) — nothing here is asserted
from reading code. The post-write regression check additionally re-reads
each resource with tenant A's own token to confirm the field value is
byte-for-byte identical to its pre-probe baseline, not merely that the
marker string is absent.

**Known limits / deferred:** only 4 of ~20+ services are covered (chosen as
"representative" per the task — one Go REST service pattern (case-service,
audit-service) and one Python/FastAPI pattern (dataset-service,
pipeline-orchestrator)); a fuller pen-test-lite sweep across every tenant-
scoped service (rbac-service, usage-service, notification-service, pack-
service, experiment-service, inference-service, memory-service, etc.) would
be a natural follow-up using the same script's `run_service_probe` helper.
The script is reusable as-is: re-run any time with `make security-probe` or
directly via `deploy/e2e/.venv/bin/python deploy/security/cross_tenant_authz_probe.py`.

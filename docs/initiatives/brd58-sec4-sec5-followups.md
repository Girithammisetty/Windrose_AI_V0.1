# BRD 58 WS1 — SEC-3 (headers + CORS), SEC-4 (RLS NULLIF re-remediation), SEC-5 (residual injection edges)

**Status:** done — 2026-07-22 (SEC-2 remains open, tracked directly in BRD 58 WS1)
**Related:** [58_production_hardening_BRD.md](../brd/58_production_hardening_BRD.md) WS1 · [rbac-dlq-envelope-tenant-id](rbac-dlq-envelope-tenant-id.md)

Filed as a standalone initiative doc rather than appended directly to
`docs/brd/58_production_hardening_BRD.md` because that file had concurrent
in-flight edits from a parallel session at the time this work landed — this
doc should be folded into BRD 58's own Implementation & Test log the next time
that file is safely editable (its WS1 checklist items for SEC-4/SEC-5 should
flip to `[x]` at the same time).

---

## 1. Analysis

### 1a. Product
A pentest/security review must pass before customer install (BRD 58's own
framing). SEC-4 and SEC-5 are two of the four remaining WS1 gaps (SEC-2 and
SEC-3 are tracked separately, still open).

### 1b. Technical (audited before any fix, via a dedicated research pass)

**SEC-3:** `ui-web/src/middleware.ts` only sets any security header on the
`/embed/*` branch (a per-tenant `Content-Security-Policy: frame-ancestors`);
the main interactive app gets none at all, and middleware's own route
`matcher` explicitly excludes `/login`, `/api`, and static assets anyway, so
even adding headers there wouldn't cover every response. `bff-graphql/src/
index.ts` is a raw `http.createServer` (no Express/helmet/cors) with zero
CORS handling and no `OPTIONS` preflight response at all.

**SEC-4:** migration `0005` (agent-runtime) converged tenant-isolation
policies on `(NULLIF(current_setting('app.tenant_id', true), ''))::uuid` — a
plain cast throws `invalid input syntax for type uuid: ""` once a pooled
connection's transaction-local `set_config` reverts to an empty string at
session level. Three later migrations reintroduced the unsafe plain-cast form
for five new tenant tables: `agent_transcripts` (0006), `sft_datasets` /
`sft_examples` (0007), `slm_training_jobs` / `slm_adapters` (0012). Grepped
all 17 migration files for `current_setting('app.tenant_id'` to confirm this
is the complete list — no other migration adds a policy referencing it.

**SEC-5** (four separate root causes, each verified directly, not assumed):
1. **DNS-rebind TOCTOU** — `libs/go-common/httpx/ssrfguard.go`'s `GuardURL`
   resolves and validates a hostname once; `audit-service/internal/siemexport/
   delivery.go`'s `Deliver` discarded the returned IPs and built its HTTP
   request against a plain `&http.Client{}`, so the real TCP connection
   re-resolves the hostname at dial time — a second, unguarded DNS lookup.
   `notification-service`'s original webhook sender already closes this via a
   pinned-IP `DialContext`; audit-service's newer (BRD 59 WS2) caller didn't
   port that part of the pattern.
2. **String-built SQL, DuckDB browse** — `dataset-service/app/adapters/
   duckdb_browse.py`'s `_configure_s3` spliced tenant-configured S3 settings
   (region/endpoint/access_key/secret_key) into `SET s3_x='{value}'` with no
   quote escaping, despite the same file already having a `_q()` escaping
   helper used elsewhere.
3. **String-built SQL, warehouse preview drivers** — `ingestion-service`'s
   `preview()` on **7** DB-backed drivers (bigquery, dbapi, mssql, spanner,
   postgres, oracle, mysql) all built `f"SELECT * FROM {request['table']}"`
   with zero identifier quoting, despite the real `execute()` path already
   using typed out-of-band parameters for values (`ING-FR-061`) — the table
   *identifier* was never covered by that guarantee.
4. **Regex-only PII redaction** — `agent-runtime/app/domain/redact.py` and
   `ai-gateway/app/adapters/guardrail_models.py` both redact
   email/phone/SSN/card/IP but had no address or person-name pattern at all;
   ai-gateway's `PERSON` entry was literally `re.compile(r"$^")` — a pattern
   that can never match anything.

---

## 2. Design

- **SEC-3 (ui-web):** static headers via `next.config.mjs`'s `headers()`
  (applies to every response Next.js serves, unlike middleware's matcher) --
  `X-Content-Type-Options`, `Strict-Transport-Security`, and for every route
  EXCEPT `/embed/*`: `X-Frame-Options: DENY` + `Content-Security-Policy:
  frame-ancestors 'none'`. `/embed/*` is deliberately excluded from that rule
  because middleware.ts already sets its OWN dynamic per-tenant
  `frame-ancestors` there -- multiple CSP headers on one response are ANDed
  per directive by the browser, so a second global `frame-ancestors 'none'`
  would silently block all legitimate embedding. Scoped deliberately narrower
  than a full script-src/style-src CSP: this app loads third-party image URLs
  (tenant logos from MinIO via the branding proxy), SSE (realtime-hub), and
  various fetches -- a strict content policy is a much larger, higher-
  regression-risk initiative that needs its own dedicated pass, not squeezed
  into a security-headers fast-follow. Documented here as a deliberate scope
  cut, not a silent gap.
- **SEC-3 (bff-graphql):** a new `corsAllowedOrigins: string[]` config field
  (`CORS_ALLOWED_ORIGINS` env, comma-separated, defaulting to ui-web's own dev
  origin) and a small hand-rolled `applySecurityAndCors()` helper (no
  Express/helmet available) called first in the raw `http.createServer`
  handler: sets `X-Content-Type-Options`/`X-Frame-Options` on every response;
  reflects `Access-Control-Allow-Origin` only when the request's `Origin` is
  on the allowlist (never a wildcard); answers `OPTIONS` with 204 directly,
  never reaching JWT auth or GraphQL execution.
- **SEC-4:** pure forward-only SQL migration (`0018`), re-applying 0005's
  exact `NULLIF(...)` policy form to the five regressed tables. No
  application code change — this is policy-only remediation.
- **SEC-5.1:** add `httpx.PinnedClient(ips, timeout)` to the shared
  `libs/go-common/httpx` package (parameterized port of notification-service's
  private `pinnedClient`), and use it in `audit-service`'s `Deliver` with the
  IPs `GuardURL` already resolved — one shared implementation instead of a
  second bespoke one.
- **SEC-5.2:** quote every interpolated S3-config value in `_configure_s3`
  via the file's own existing `_q()` helper (`use_ssl` excluded — it's a
  boolean this function derives itself, never round-tripped from `s3`).
- **SEC-5.3:** one shared `quote_identifier(name, quote=...)` /
  `quote_bracket_identifier(name)` helper in `app/domain/drivers/sql.py`
  (which already houses the other cross-driver SQL-shaping helpers),
  splitting on `.` so a dotted `schema.table`/`project.dataset.table` gets
  each part quoted independently; applied identically across all 7 drivers
  with the dialect-correct quote char (backtick: bigquery/spanner/mysql;
  double-quote: postgres/oracle/dbapi[snowflake+redshift+databricks];
  bracket: mssql).
- **SEC-5.4:** a real, narrow structural floor — honorific-prefixed name
  (`Mr./Dr./Ms. Jane Doe`) and US street-address (`742 Evergreen Terrace`)
  patterns — added identically to both redaction modules. Deliberately NOT a
  claim of general NER-level name/address detection (that still needs
  Presidio+spaCy, unchanged, and is called out as such in both modules'
  docstrings) — a real, if narrow, capability rather than a fabricated one.

---

## 3. Implementation & Test

**SEC-3** — `services/ui-web/next.config.mjs` (+`headers()`),
`services/bff-graphql/src/config.ts` (+`corsAllowedOrigins`),
`services/bff-graphql/src/index.ts` (+`applySecurityAndCors`). **Test:** new
`services/bff-graphql/tests/integration/corsAndSecurityHeaders.test.ts` boots
the real BFF HTTP server and drives it over real HTTP (5 cases: allowlisted
origin reflected + preflight short-circuits before auth, a second configured
origin also reflected, a non-allowlisted origin gets no CORS header but the
request still serves normally, never a wildcard, static headers present on
every response). bff-graphql full suite: 36 unit files/296 tests +
2 integration files/11 tests, all passing; `tsc --noEmit`/`eslint` clean.
**Live-verified against the real, restarted stack** (ui-web's Next dev server
auto-restarted itself on the `next.config.mjs` change; bff-graphql needed a
manual restart via `restart_bff.sh`, done with explicit user confirmation):
`curl` against the running services confirmed (a) the main ui-web app serves
`X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'`
while `/embed/*` gets neither (only its own dynamic
`frame-ancestors 'self'`/tenant-origin CSP from middleware), (b) bff-graphql's
`OPTIONS /graphql` preflight from the allowed origin returns 204 with the
correct `Access-Control-*` headers, an unlisted origin gets served with zero
CORS headers, and never a `*`. Reloaded `/admin/tenant` in the browser after
both restarts: zero console errors, page renders correctly -- confirms the
change didn't break the real UI→BFF traffic path.

**SEC-4** — `services/agent-runtime/migrations/versions/
0018_nullif_rls_reremediation.py`. **TDD, bug reproduced first:** stashed the
migration, ran the new `tests/integration/test_rls_reremediation.py` against
real Testcontainers Postgres — both tests failed exactly as predicted
(`invalid input syntax for type uuid: ""` on a reused pooled connection after
a transaction-local `set_config` reverted; `pg_policies.qual` had no
`NULLIF`). Restored the migration, both pass. Full `agent-runtime` unit suite:
287 passed, 0 fails.

**SEC-5.1** — `libs/go-common/httpx/ssrfguard.go` (+`PinnedClient`),
`services/audit-service/internal/siemexport/delivery.go` (now pins to
`GuardURL`'s resolved IP; `HTTPDelivery.Client` field replaced with
`Timeout time.Duration` since the client is now built per-delivery).
**Test:** new `libs/go-common/httpx/pinned_client_test.go` proves the dialer
genuinely ignores the request's own host — a request built against a
non-resolving hostname succeeds when pinned to the real server's IP, and a
request built against the real, reachable host fails when pinned to a bogus
IP (`TestPinnedClientDialsOnlyThePinnedIP`, both subtests). Existing
`audit-service/internal/siemexport` delivery tests (SSRF-block, cross-tenant,
CEF/JSON content-type) all still pass unmodified.

**SEC-5.2** — `services/dataset-service/app/adapters/duckdb_browse.py`.
**TDD, bug reproduced first:** new `tests/unit/test_duckdb_browse_s3_config.py`
run against the unfixed file (`git stash`) — a region value containing
`'; CREATE TABLE injected(x INTEGER); --` genuinely executed the injected
`CREATE TABLE`/follow-on `DROP TABLE` (proven by the resulting
`CatalogException: Table with name foo does not exist!`, which only occurs if
the injected `DROP TABLE foo` statement actually ran); an endpoint value with
an embedded quote was silently truncated at the quote instead of erroring.
Restored the fix: all 3 new tests pass, the malicious payload round-trips
intact as a literal, inert string via `current_setting(...)`.

**SEC-5.3** — `app/domain/drivers/sql.py` (+`quote_identifier`,
`quote_bracket_identifier`) and all 7 preview() call sites (`bigquery.py`,
`spanner.py`, `mysql.py`, `postgres.py`, `oracle.py`, `dbapi.py`, `mssql.py`).
**Test:** new unit tests in `tests/unit/test_driver_sql.py` (dotted-part
quoting, embedded-quote escaping for both backtick and double-quote dialects,
bracket escaping). Full `ingestion-service` unit suite: 542 passed (was 539),
0 fails — confirms none of the 7 touched drivers' existing contract tests
assumed the old unquoted SQL shape.

**SEC-5.4** — `services/agent-runtime/app/domain/redact.py` and
`services/ai-gateway/app/adapters/guardrail_models.py` (`ADDRESS`/`PERSON`
patterns + corrected docstring — the old `PERSON` regex could never match,
which the previous comment didn't make clear). **Test:** 2 new cases in
`agent-runtime/tests/unit/test_transcripts.py` (street address, honorific
name); new `ai-gateway/tests/unit/test_guardrail_pii.py` (5 cases — first
test coverage `RegexPIIAnalyzer` has ever had at all — including an explicit
negative case proving the PERSON floor is deliberately narrow: a bare
capitalized name with no honorific is *not* caught). Full suites:
`agent-runtime` 289 passed, `ai-gateway` 153 passed, 0 fails either.

**Deferred, explicitly:** SEC-2 (audit→WORM delivery reconcile) remains open —
tracked directly in BRD 58 WS1, not duplicated here.

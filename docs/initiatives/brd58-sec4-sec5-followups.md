# BRD 58 WS1 — SEC-4 (RLS NULLIF re-remediation) + SEC-5 (residual injection edges)

**Status:** done — 2026-07-22
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

**Deferred, explicitly:** SEC-2 (audit→WORM delivery reconcile) and SEC-3
(security headers + CORS allowlist) remain open — tracked directly in BRD 58
WS1, not duplicated here.

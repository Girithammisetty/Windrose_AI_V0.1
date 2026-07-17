# case-service (Go) — claims triage

The claims-triage service (BRD 08): it owns row-reference triage cases generated
from query/inference result rows, the case lifecycle state machine, assignment +
durable SLA timers, dispositions, custom fields, comments/activity timeline, bulk
operations, the OpenSearch-backed list/search projection, and the triage-copilot
proposal-application endpoints that turn a human correction into a labeled
training signal.

Row-reference model (CASE-FR-001): a case stores `dataset_urn` + `dataset_version`
+ `row_pk` + a small display projection — **never** a full-row snapshot while
open. The full row is fetched live from query-service on read, and archived to
object storage exactly once at closure.

## Run

```bash
export PATH="/opt/homebrew/bin:$PATH"
# Bring up the dev stack (Postgres, Redis, Redpanda, OPA, OpenSearch, …)
docker compose -f ../../deploy/docker-compose.dev.yml up -d postgres redis redpanda opa opensearch
make build          # go build ./...
make run            # DATABASE_URL, OPENSEARCH_URL, KAFKA_BROKERS, OPA_URL, REDIS_ADDR, JWKS_URL from env
```

Key env: `DATABASE_URL` (default `postgres://windrose:windrose_dev@localhost:5432/case`),
`OPENSEARCH_URL` (`http://localhost:9200`), `KAFKA_BROKERS` (`localhost:9092`;
`false` = in-memory dev publisher), `OPA_URL` (`http://localhost:8281`),
`REDIS_ADDR` (`localhost:6379`), `QUERY_SERVICE_URL` (live row fetch),
`SNAPSHOT_ROOT` (closure snapshots), `JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`.

## Test

```bash
make test-unit          # no infra: state machine, dedup, projection, authz matrix
go test -race ./internal/domain/...   # race gate on the domain/state package
make test-integration   # Testcontainers Postgres + REAL Redpanda/OpenSearch/Redis
make test               # both tiers
```

The integration tier auto-skips with a clear message when Docker or the dev-stack
infra (`localhost:9200`, `:6379`, `:9092`) is unreachable.

## Adapter inventory (every runtime adapter is real — no stubs, CONVENTIONS END STATE)

| Capability | Adapter | Real backend |
|---|---|---|
| OLTP + RLS | `internal/store` (pgx) | PostgreSQL 16, `app.tenant_id` GUC + FORCE RLS |
| Event bus (emit) | `internal/events` KafkaPublisher + outbox relay | Redpanda (Kafka API) at `:9092` |
| Search projection | `internal/search` (opensearch-go/v3) + Kafka index consumer | OpenSearch 2.17 at `:9200` |
| AuthZ | `internal/authz` OPAClient | OPA sidecar `:8281` + Redis `permissions_flat` projection |
| AuthN | `internal/api` JWKS verifier (RS256, alg=none rejected) | identity-service JWKS |
| Consumer dedup / rate | go-common redisx | Redis 7 at `:6379` |
| Durable SLA timers | `internal/sla` Postgres-backed sweep worker | `sla_timers` table (Temporal-equivalent) |
| Bulk concurrency gate | `internal/api acquireBulkSlot` | Redis (`INCR`/`DECR`, max 5/tenant, CASE-FR-032) |
| Live row fetch | `internal/api` HTTPRowFetcher | query-service (`?with_row=true`, BR-5 degrades to `row_error`) |
| Closure snapshot + CSV export | `internal/api` FSSnapshotStore (gzip) | local object root; **MinIO/S3 is the drop-in production adapter** |

**Dev-only, non-default double (explicitly gated):** `KAFKA_BROKERS=false` selects
an in-memory event publisher (`events.NewInMemory`) for broker-less local dev. It
is **not** the default (default `localhost:9092` → real Redpanda), is never
selected unless the operator sets the flag, is logged with a `WARN` at startup,
and is the only non-durable substitute reachable from `cmd/server`. Every other
runtime adapter is real.

### Documented exceptions / deferrals

- **Temporal**: Temporal was not running at `:7233` in this environment, so SLA
  enforcement uses the **real Postgres-backed durable sweep worker** (`internal/sla`)
  per the BRD's stated fallback. Timer state lives in `sla_timers`, so it survives
  restarts — proven by `TestAC4`. Not a stub; a durable mechanism.
- **Closure snapshots & CSV exports** use a filesystem gzip object store (real
  bytes on disk). The `SnapshotStore` interface swaps to MinIO/S3 unchanged; wire
  shapes are identical (`snapshots/<tenant>/<case>.json.gz`,
  `exports/<tenant>/<op>.csv.gz`).
- **`cases` table is not month-partitioned** (unlike `case_events`, which is): the
  unique dedup/case-number constraints require a non-partitioned parent; retention
  is a documented purge job. `case_events` **is** monthly-partitioned (MASTER-FR-062).
- **Soft case-limit warning event** (`case.limit.warning` at 80%) is deferred; the
  hard limit (BR-13) at 100% is enforced. This is the only remaining functional
  deferral — CSV export (CASE-FR-044) and filter-based async bulk (CASE-FR-030) are
  now fully implemented with real object storage / OpenSearch resolution.

## Learning-loop hook (how a human correction becomes a training signal)

When a human resolves a case with a disposition — directly (`POST /cases/:id/resolve`)
or by applying an approved copilot proposal (`POST /cases/:id/apply-proposal`) — the
service emits **`case.disposition_applied`** on `case.events.v1`, carrying the row
reference and the decision:

```
{ dataset_urn, dataset_version, row_pk,
  disposition: { id, code, category },
  resolution_note, severity }
actor = { type: user, id: <approver> }        # the human who decided
via_agent = { agent_id, version } | null        # the copilot, when proposal-driven
```

That tuple is exactly a labeled example on a specific dataset row: the learning
loop keys on `dataset_urn`+`row_pk` (the same identity as the dedup key) and reads
`disposition.category` as the label, with `via_agent` distinguishing
agent-assisted from purely-human corrections. When the correction is
proposal-driven, an additional **`case.correction_recorded`** event (and a
timeline entry linking `proposal_urn` + `via_agent`) records the dual attribution
for audit. See `internal/api/handlers_transitions.go::resolveMutation`.

## FR coverage

**Implemented (Must + Should):** CASE-FR-001..006 (row-reference, creation from
rows + inference, case_number, dedup/recurrence, closure snapshot), 010..015
(status enum + invariant, assignment, SLA via durable timers with a reachable
escalation ladder, timer lifecycle, reopen, escalate), 020..025 (dispositions,
severity, custom fields + values, comments, timeline), 030..032 (bulk ids +
filter-based async, partial failure, batch cap, Redis concurrency gate), 040..044
(OpenSearch index/projection/search/facets/reindex, CSV export to object storage),
050..052 (copilot read surface via case read APIs, proposal application, field
whitelist).

**Deferred (documented above):** soft `case.limit.warning` event only.
Count: **~32 FRs implemented, 1 minor deferral.**

## FR / AC → code + test traceability

| FR / AC | Code | Test |
|---|---|---|
| CASE-FR-001 row-reference | `domain/types.go`, `store/pg_cases.go` | `TestAC1_RowReferenceCreation` |
| CASE-FR-002 create from rows | `api/handlers_cases.go` | `TestAC1` |
| CASE-FR-003 inference auto-case | `cmd/server/main.go creatorAdapter`, `events/consumers.go` | (consumer wired; unit-covered by envelope handler) |
| CASE-FR-004 case_number | `store/pg_cases.go nextCaseNumber` | `TestAC1` (sequential) |
| CASE-FR-005 dedup / recurrence | `domain/DedupKey`, `store/pg_cases.go` | `TestDedupKey`, `TestAC2_Dedup` |
| CASE-FR-006 closure snapshot | `api/handlers_transitions.go handleClose`, `api/adapters.go` | `TestAC8_ClosureSnapshot` |
| CASE-FR-010 status + invariant | `domain/statemachine.go`, migration CHECK | `TestStateMachine_*`, `TestAC5` |
| CASE-FR-011 assign/unassign | `domain`, `api/handlers_transitions.go` | `TestAC5`, `TestAC6` |
| CASE-FR-012/013 durable SLA + escalation ladder | `internal/sla`, `store/pg_sla.go FireDueTimer` | `TestAC3_SLAAutoUnassign`, `TestAC4_SLADurableAcrossRestart`, `TestSLAEscalationLadderReachable`, `TestSweepDispatch` |
| CASE-FR-014 reopen ≤30d | `domain/statemachine.go Reopen` | `TestStateMachine_TransitionMatrix` |
| CASE-FR-020 dispositions | `store/pg_catalog.go`, `api/handlers_catalog.go` | `TestAC5`, `TestAC8` |
| CASE-FR-022 custom fields + shadowing | `store/pg_catalog.go ListFields`, `api handleForm` | `TestAC12_FormFieldsShadowing` |
| CASE-FR-023 field validation | `api/handlers_common.go validateCustomFields` | covered via create/patch |
| CASE-FR-024/025 comments + timeline | `store/pg_catalog.go`, `api/handlers_comments.go` | `TestAC3`, `TestAC10` (timeline asserts) |
| CASE-FR-030/031 bulk (ids + filter async) + partial failure | `api/handlers_bulk.go` | `TestAC6`, `TestAC7_BatchTooLarge`, `TestBulkByFilterAsync` |
| CASE-FR-032 bulk concurrency gate | `api/handlers_bulk.go acquireBulkSlot` (Redis) | `TestAcquireBulkSlotNoRedis` (unit) + real Redis in runtime |
| CASE-FR-040/041 OpenSearch projection | `internal/search`, Kafka index consumer | `TestAC9_SearchProjectionWithinWindow` |
| CASE-FR-042 search + facets | `search/query.go` | `TestAC9`, `TestExpandStatus` |
| CASE-FR-043 reindex + alias swap | `search/opensearch.go Reindex`, `api handleReindex` | (endpoint) |
| CASE-FR-044 CSV export to object storage | `api/handlers_bulk.go handleExport`, `store ExportCases`, `FSSnapshotStore` | `TestExportCSVReal`, `TestGzipCSV` |
| CASE-FR-051 proposal apply | `api/handlers_proposal.go` | `TestAC10_ApplyProposalDualAttribution` |
| CASE-FR-052 field whitelist | `api/handlers_proposal.go allowedProposalFields` | `TestAC11_ProposalFieldNotAllowed` |
| MASTER RLS / cross-tenant 404 | `store` `app.tenant_id` + FORCE RLS | `TestAC13_CrossTenantIsolation` |
| BR-10 search-down → 503 | `api/handlers_cases.go handleSearchCases` | `TestAC14_SearchUnavailable` |
| BR-11 projection truncation | `domain/TruncateProjection` | `TestTruncateProjection` |
| MASTER authz matrix | `internal/authz` | `TestStaticAuthzMatrix` (unit) + real OPA in runtime |

## Events

Emitted on `case.events.v1` (Avro envelope `events/case_event.avsc`): `case.created`,
`case.assigned`, `case.unassigned`, `case.started`, `case.resolved`, `case.reopened`,
`case.closed`, `case.escalated`, `case.sla.warning`, `case.sla.breached`,
`case.comment.added`, `case.severity.changed`, `case.bulk.completed`,
`case.disposition_applied`, `case.correction_recorded`. Consumed:
`inference.events.v1/inference.completed` (auto-case), `identity.events.v1/user.deactivated`
and `rbac.events.v1/workspace.member.removed` (unassign the user's open cases).

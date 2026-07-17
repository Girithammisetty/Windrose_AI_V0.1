# Design — Decision Write-Back (System-of-Record Sync)

## Problem

Windrose is a decision platform, but data flow is one-way. The 19 connectors
**pull** a tenant's data in (every connector config is "the database/schema to
read from"); the platform then produces decisions (case dispositions, approved
proposals, inference scores); but there is **no path to write those decisions
back** to the tenant's system of record (SoR). A denial or an approved
prior-auth is stranded inside Windrose — the only egress today is a manual
"Export CSV" on the Cases page.

This is a named, deliberately-deferred requirement, not a new idea. Every
vertical pack defers two kinds:
- `connection_templates` (INS-FR-060) — read-side SoR connectors.
- `write_adapters` (INS-FR-061) — *"SoR write adapters (Facets PA decision,
  Guidewire notes, EDI responses) execute only behind pack-service's
  materialization contract and customer credentials; **all platform-side
  writes remain proposal-mode.**"*

## Home: ingestion-service (not a new service)

The `connections` model is **already direction-aware**:
`traffic_direction ∈ {incoming, outgoing, both}` (default `incoming`). Write-back
is the *outgoing* counterpart of ingestion. ingestion-service already owns:
connection CRUD, the vault-backed secret machinery, tenant/workspace RLS, the
action self-registration path (`registration.py` → rbac catalog), and boot
wiring. So we **extend** an already-booted, already-cataloged service rather
than standing up a new one.

## Model

- **Target = an `outgoing` connection** (reuse `connections`). Its config
  carries the write destination:
  - `db_upsert`: `{ target_schema, target_table, key_column, column_mapping }`
    + a real DSN (from the connection's postgres config / vault).
  - `http_post`: `{ url, method, headers, body_template? }`.
- **`writebacks`** (new table, tenant/workspace-scoped, RLS) — the durable
  job/delivery record:
  `id, tenant_id, workspace_id, connection_id, decision_kind (e.g.
  "case.disposition"), decision_ref (case URN), idempotency_key, payload
  (jsonb decision snapshot), status, approval_mode, requested_by, approved_by,
  attempts, last_error, target_ref, created_at, updated_at, delivered_at`.
  - `status`: `pending_approval → approved → delivering → delivered | failed`,
    plus `rejected`. Idempotent by `(tenant_id, connection_id, idempotency_key)`.

## Governance (the non-negotiable part)

Writing into a tenant's production SoR is the highest-stakes, most irreversible,
outward-facing action the platform takes. Therefore:
- **Proposal-mode / four-eyes by default.** A write-back enters
  `pending_approval`; a **distinct** approver (≠ the requester) must approve
  before any external write. An adapter may be configured `approval_mode=auto`
  only for reversible, low-risk targets.
- **Idempotency.** Exactly-once: `db_upsert` uses `ON CONFLICT (key_column)`;
  `http_post` sends an `Idempotency-Key` header. The job's idempotency_key
  dedups re-enqueues.
- **Durability + retry.** Delivery status is persisted; a failed delivery is
  `failed` with `last_error` and is retryable; nothing is lost on crash.
- **Provenance + audit.** Every delivery emits an audit event (actor, approver,
  decision_ref, target, outcome). The case shows synced / pending / failed.
- **Least privilege + credentials.** The target's write credentials live in the
  vault, scoped to the specific target; the platform runtime never sees them in
  plaintext (dev/e2e uses a real local Postgres DSN).

## RBAC actions (self-registered, workspace-scoped)

`ingestion.writeback.create`, `ingestion.writeback.read`,
`ingestion.writeback.approve`, `ingestion.writeback.execute` — added to
`registration.py::MANIFEST` so rbac catalogs them at boot (avoids the OPA
`action_known` gap).

## API (ingestion-service)

- Outgoing-connection CRUD: reuse existing `/connections` with
  `traffic_direction=outgoing`.
- `POST /writebacks` — enqueue `{connection_id, decision_kind, decision_ref,
  idempotency_key, payload}`. → `pending_approval` (or `delivering` if auto).
  Needs `ingestion.writeback.create`.
- `GET /writebacks` (status filter) / `GET /writebacks/{id}` — needs
  `ingestion.writeback.read`.
- `POST /writebacks/{id}/approve` — four-eyes (approver ≠ requester) → deliver.
  Needs `ingestion.writeback.approve`.
- `POST /writebacks/{id}/reject`.
- `POST /writebacks/{id}/retry` — re-deliver a failed job. Needs
  `ingestion.writeback.execute`.

## Executors (real, no stubs)

- **`db_upsert`** — real SQL `INSERT ... ON CONFLICT (key_column) DO UPDATE`
  into the target table under the target DSN. Idempotent, transactional.
- **`http_post`** — real `httpx` POST/PUT to the target URL with an
  `Idempotency-Key` header; 2xx = delivered, else `failed` with the response.

## UI

- **Sync a decision**: on a resolved case, "Sync to system of record" → picks
  an outgoing target → `POST /writebacks` → shows status.
- **Write-backs list**: an admin screen showing every job with its status
  (pending / delivered / failed) + approve/retry actions.

## Status

**Increment 1 (backend hero path) — BUILT + VERIFIED (2026-07-15).** In
ingestion-service: `0007_writebacks` migration (+RLS), `Writeback` ORM model,
`WritebackService` (enqueue/list/get/approve/reject/retry + `db_upsert` and
`http_post` real executors), `writebacks` routes, `ingestion.writeback.*`
actions self-registered, four-eyes + idempotency + audit. 4 integration tests
(real local HTTP sink delivery, four-eyes rejection, idempotent enqueue,
reject, outgoing-only) + full ingestion unit suite 379 green. **Live e2e on the
stack**: an outgoing Postgres connection → enqueue a `case.disposition` decision
→ requester self-approve blocked (422) → distinct approver approves → the
decision lands as a real row in the tenant SoR table (`sor_claim_decisions`:
`CLM-4406 | denied | prior-auth not on file`); target_ref
`public.sor_claim_decisions[case_id=CLM-4406]`; credentials round-tripped
through the real vault. **Increment 2 (BFF + UI) — pending.**

## Build order (increments)

1. **Backend hero path (this increment):** `writebacks` migration + model +
   domain service + routes + both executors + RBAC actions + audit +
   idempotency + four-eyes. Tests. **Live e2e:** create an outgoing postgres
   target table → enqueue a write-back from a real resolved case → approve
   (four-eyes) → the decision row lands in the real target table → status
   `delivered`. Verified by querying the target table.
2. **BFF + UI:** expose writebacks (list/get/create/approve/retry) + outgoing
   connection create; a "Sync decision" case action + a write-backs list.
3. **Follow-ups (not this increment):** Kafka decision-event auto-trigger,
   async delivery worker (vs. inline-on-approve), vault-scoped credentials,
   read-back confirmation, vendor adapters (Facets/Guidewire/EDI) behind
   pack-service.

## Explicitly out of scope / honest limits

- No vendor SoR SDKs (Facets/Guidewire/EDI) — those need customer credentials +
  pack-service materialization. The two adapters here (db_upsert, http_post)
  prove the whole governed loop end-to-end on the local stack without any
  external dependency.
- Delivery is **inline on approve** in increment 1 (with a durable job record +
  retry). A dedicated async delivery worker is a follow-up; the job model is
  already worker-ready.

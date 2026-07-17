# agent-runtime + agent-registry (BRD 14)

One bounded context, two concerns:

- **agent-runtime** — executes agents: **LangGraph graphs wrapped in Temporal
  workflows** (durable runs, retries, timers, HITL signals), the **session model**,
  and the **proposal/HITL framework** that is the only path for agent writes.
- **agent-registry** — agent definitions, immutable **versions** (graph ref +
  prompt refs + toolset + model + eval-gate), agent principals, per-tenant
  pinning/canary/shadow/rollback, per-(version×tenant) kill switches, and signed
  **A2A cards**.

This is the capstone that closes the claims **learning loop**: the **case-triage
copilot** proposes a claim disposition, a human approves, and on approval
agent-runtime **issues the signed proposal-execution grant** that tool-plane
verifies before the write executes. The **governance agent** opens retrain
proposals from drift/correction signals.

```
app/
  api/            FastAPI routes (chat, sessions, runs, proposals, registry, a2a, jwks) + auth + errors
  domain/         entities, canonical args-digest, policy (auto-exec matrix), urn, errors, ports
  signing/        RSA keys + JWKS, proposal-execution GRANT issuer, A2A card signer, OBO token minter
  proposals/      proposal framework + HITL decide + grant issuance + tool-plane execution
  graphs/         LangGraph agents: triage.v1 (priority), governance.v1 (priority), analytics.v1
  agents/         catalog seed (8 definitions; real published graphs for the priority agents)
  runtime/        run engine + orchestrator; temporalx/ (AgentRunWorkflow, activities, worker)
  adapters/       REAL: ai-gateway LLM, tool-plane MCP, memory-service, case-service, realtime-hub,
                  OPA authz, Redis kill registry, Kafka bus; fakes.py = unit-tier doubles ONLY
  store/          sql.py (Postgres RLS store) + memory.py (unit double)
  container.py    real-by-default wiring    main.py  app factory (+ in-process Temporal worker)
migrations/  events/*.avsc  api/openapi.yaml  Makefile  Dockerfile
```

## Run (real adapters by default — CONVENTIONS END STATE)

Infra: `deploy/docker-compose.dev.yml` (Postgres **pgvector**, Redis, Redpanda, OPA)
plus **Temporal** (`:7233`), **ai-gateway** (`:8092` → Ollama `qwen2.5:0.5b`),
**tool-plane** mcp-gateway (`:8091`), **case-service** (`:8084`), **memory-service**,
**realtime-hub**.

```
make install
make migrate          # alembic upgrade head (needs pgvector Postgres)
make run              # AR_USE_REAL_ADAPTERS=true uvicorn app.main:app :8086 (+ in-process worker)
make worker           # standalone Temporal worker (optional; run also runs one in-process)
```

`app.main:app` builds a fully **real** container by default (SQL store + ai-gateway
LLM + tool-plane tools + Kafka + Redis + OPA + Temporal + RS256 grant signing). No
in-memory double is reachable from the running binary. `AR_USE_REAL_ADAPTERS=false`
is set **only** by the unit tier.

## Test

```
make test-unit         # no external deps (in-memory doubles inside tests/ only)
make test-integration  # Testcontainers pgvector + live Temporal/Kafka/OPA/ai-gateway/tool-plane; auto-skips if down
make no-stub-gate      # greps runtime source for forbidden stub markers -> must be clean
```

## The proposal-execution grant (issuing format) — tool-plane TPL-FR-035

agent-runtime is the **ISSUER**. On human **approve/edit_args** (or a tenant
auto-execute policy hit), `ProposalService` mints an **RS256-signed JWS** and
presents it to tool-plane in the MCP `tools/call` field **`params._meta.proposal_grant`**
(not a header). tool-plane fetches our public key from `PROPOSAL_JWKS_URL`
(`GET /api/v1/.well-known/jwks.json`) and verifies it.

Header: `{ "alg": "RS256", "kid": "agent-runtime-2026-1" }`

Claims (exactly what `tool-plane/internal/authz/proposal.go` verifies):

| Claim | Value |
|---|---|
| `iss` | `windrose-agent-runtime` (constant `GRANT_ISSUER`; tool-plane pins this) |
| `sub` | the human decider (`decided_by`) |
| `exp` / `iat` | short-lived (120 s default) |
| `proposal_id` | approved proposal id |
| `tenant_id` | tenant uuid |
| `tool_id` | e.g. `case.apply_disposition` |
| `tier` | effective tier, e.g. `write-proposal` |
| `args_digest` | `hex(sha256(canonical_json(args)))` |

`args_digest` is **byte-compatible** with tool-plane's Go `domain.ArgsDigest`
(sorted-key, whitespace-free JSON with Go HTML-escaping) — `app/domain/canonical.py`.
tool-plane binds the grant to `(tenant, tool, tier, args_digest)`; any mismatch,
bad signature, wrong issuer, or expiry → `PROPOSAL_REQUIRED` (a forged/absent grant
can never execute a write).

## Adapter inventory (every adapter real; no runtime stubs)

| Capability | Real adapter | Where |
|---|---|---|
| OLTP + tenant isolation | PostgreSQL + **RLS** (non-privileged `agent_runtime_app` role) | `store/sql.py`, `migrations/0001` |
| Durable workflows | **Temporal** (AgentRunWorkflow, per-run, HITL signals, timers) | `runtime/temporalx/*` |
| LLM (all calls) | **ai-gateway** → Ollama `qwen2.5:0.5b` (OpenAI-compatible, metered) | `adapters/llm.py` |
| Tools (all calls) | **tool-plane** mcp-gateway `/mcp` JSON-RPC + signed grant | `adapters/tools.py` |
| Grant / card signing | **RS256** (cryptography), JWKS endpoint | `signing/*`, `api/routes/jwks.py` |
| RAG grounding | **memory-service** retrieval | `adapters/memory.py` |
| Claim case read | **case-service** REST | `adapters/case.py` |
| Stream relay | **realtime-hub** publish (`agent_run:<run_id>`) | `adapters/realtime.py` |
| Authorization | **OPA** sidecar (approver eligibility) | `adapters/authz.py`, py-common opaclient |
| Kill switch | **Redis** set + pub/sub, Postgres-durable | `adapters/killswitch.py` |
| Event bus | **Redpanda/Kafka** idempotent producer + outbox | `events/bus.py`, py-common |
| AuthN | **RS256 JWT** via py-common (JWKS/static) | `api/auth.py` |

Fakes exist only in `adapters/fakes.py` + `store/memory.py`, reachable only from
`tests/` (unit tier sets `use_real_adapters=false`).

## FR coverage (BRD 14 §3)

| FR | Status | Code / test |
|---|---|---|
| ART-FR-001/002 agent definition + immutable version | ✅ | `domain/entities.py`, `store/sql.py`, `migrations/0001` (immutability trigger), `api/routes/registry.py` |
| ART-FR-003 agent principal ref | ✅ | `agents/catalog.py`, version `principal_ref` |
| ART-FR-004 per-tenant config (pin/persona/auto-exec) | ✅ | `tenant_agent_configs`, `PUT /registry/tenants/self/agents/:key` |
| ART-FR-005 toolset in version | ✅ | version `toolset` (validation hook documented) |
| ART-FR-010 Temporal workflow per run, checkpointer | ✅ | `runtime/temporalx/workflows.py`, `activities.save_checkpoint`, `test_temporal.py` |
| ART-FR-011 run lifecycle + events | ✅ | `runtime/engine.py emit_run`, `ai.agent_run.v1` |
| ART-FR-012 LLM via ai-gateway, tools via tool-plane, OBO | ✅ | `adapters/llm.py`, `adapters/tools.py`, `signing/tokens.py`, `test_triage_real_llm.py` |
| ART-FR-013 analytics graph shape (reflection loop) | ✅ (framework) | `graphs/analytics.py` |
| ART-FR-014 sanitized/server-derived inputs | ✅ | `api/routes/chat.py _inputs_from_body` (tenant from token) |
| ART-FR-016 fair-share admission (429) | ⚠️ partial | `OverCapacity` error defined; queue depth = follow-up |
| ART-FR-020..023 session model (idle/lifetime/resume) | ✅ | `runtime/orchestrator.py`, `sessions` table, `expires_hard_at` |
| ART-FR-030..032 sandbox exec | ⛔ infra-gated | gVisor is Linux/K8s-only; claims agents call **tools**, not arbitrary code — documented exception |
| ART-FR-040 8-agent catalog | ✅ | `agents/catalog.py` (triage+governance+analytics real; others defined) |
| ART-FR-041 Proposal object | ✅ | `domain/entities.Proposal`, `proposals/service.py` |
| ART-FR-042 flow + 4 decisions + durable await | ✅ | `proposals/service.decide`, `workflows.py` HITL wait, `test_proposals.py`, `test_temporal.py` |
| ART-FR-043 auto-exec matrix + destructive-never-auto | ✅ | `domain/policy.py` (3 layers), `test_policy.py` |
| ART-FR-044 approver eligibility (OPA) + self-approval | ✅ | `proposals/service._check_eligibility`, `test_proposals.py` |
| ART-FR-045 expiry/supersede | ✅ | `store.supersede_pending`, workflow expiry branch |
| ART-FR-046 proposal events (rejection/diff) | ✅ | `events/ai_proposal.avsc`, `proposals/service._emit` |
| ART-FR-050 signed A2A cards | ✅ | `signing/cards.py`, `GET /a2a/cards/:key` |
| ART-FR-051 meta-router delegation | ⚠️ partial | definition present; delegation semantics = follow-up |
| ART-FR-060 publish eval gate | ✅ | `POST .../publish` (422 EVAL_GATE_FAILED), `test_api` (422 path via registry) |
| ART-FR-061/062 canary/shadow/pin/rollback | ✅ | `rollouts`, `domain/policy.canary_assignment`, `POST /rollouts/:id/rollback` |
| ART-FR-063 kill switch (Redis pub/sub) | ✅ | `adapters/killswitch.py`, `POST /registry/kill-switches` |
| ART-FR-070..073 chat + streaming + proposal APIs | ✅ | `api/routes/chat.py`, `proposals.py`, realtime-hub publish |

## Acceptance criteria → tests

| AC | Test |
|---|---|
| AC-4 proposal → approve → attributed write (signed grant) | `test_proposals.test_approve_issues_signed_grant_and_executes`, `test_temporal` |
| AC-5 auto-exec reversible; destructive-auto 422 (3 layers) | `test_policy`, `test_api.test_destructive_auto_policy_rejected_422` |
| AC-6 decision race → exactly one wins (409) | `test_proposals.test_decision_first_wins_conflict`, `test_api` |
| AC-2 worker crash → run resumes, no dup write | `test_temporal.test_run_survives_worker_restart` |
| AC-12 approver lacks perm / self-approval → 403 | `test_proposals.test_self_approval_denied_by_default` (OPA path in `_check_eligibility`) |
| AC-14 cross-tenant → 404 (RLS) | `test_api.test_cross_tenant_proposal_is_404`, `test_rls.py` |
| triage real model → disposition proposal | `test_triage_real_llm.py` (ai-gateway→Ollama, usage tokens asserted) |
| grant accepted / forged rejected | `test_signing.py`, live Go-verifier proof (below), `test_grant_tool_plane.py` |

## Verification (this build, run live on this machine)

- **args_digest byte-match**: tool-plane's Go `domain.ArgsDigest` and this service's
  `canonical.args_digest` produce the **identical** hash for the same args
  (`35b29656…`), executed against tool-plane's own Go function.
- **Grant interop (req 2 core)**: tool-plane's REAL `authz.ProposalVerifier`
  (`NewProposalVerifierJWKS`) was pointed at this service's live JWKS endpoint and
  **accepted** a grant this service issued; **forged** (wrong key), **args-digest
  mismatch**, and **wrong-tenant** grants were all **rejected** → `PROPOSAL_REQUIRED`.
- **Real triage (req 1)**: the triage LangGraph, calling the real model **through
  ai-gateway → Ollama qwen2.5:0.5b**, produced a disposition proposal with real
  usage tokens (e.g. input 180 / output 39) and a model-written rationale.
- **Temporal durability (req 3)**: an `AgentRunWorkflow` on the real Temporal server
  paused in `awaiting_approval`; the worker was killed and a fresh worker started;
  approving resumed the run to completion and issued the signed grant.
- **RLS (req 4)**: cross-tenant reads via the non-privileged `agent_runtime_app`
  role return zero rows (Testcontainers pgvector).
- **Live probe**: `AR_USE_REAL_ADAPTERS=true uvicorn app.main:app` boots with the
  **real** container (SQL store, ai-gateway LLM, tool-plane client, Kafka, Redis,
  OPA, Temporal, RS256 signing) — no in-memory store/LLM in the runtime path.

## Documented exceptions (infra-gated only)

- **gVisor sandbox (ART-FR-030..032)**: Linux/K8s `agents-sandbox` node pool; not
  runnable on macOS. The claims agents call **governed tools**, not arbitrary code,
  so arbitrary-code-exec is deliberately **not** on the runtime path (per brief).
  The sandbox I/O contract + audit event (`ai.code_executed.v1`) are defined.
- **Full `/mcp` → case-service disposition write**: exercising the entire tool-plane
  pipeline (gateway + registered tool + backend facade + OPA rbac projections +
  case-service) requires the compose stack; `test_grant_tool_plane.py` runs it and
  auto-skips otherwise. The grant-acceptance crux is proven live against tool-plane's
  real verifier (above).

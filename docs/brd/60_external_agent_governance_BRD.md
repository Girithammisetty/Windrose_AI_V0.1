# BRD 60 — External Agent Governance

**Status:** in-progress — 2026-07-22 · inc-1 landed
**Owner:** platform · **Related:** BRD 53 (custom agents), tool-plane MCP gateway, ProposalService four-eyes, audit WORM chain, memories `project_windrose_custom_agents`, `project_windrose_ml_engineer_agent`, `project_windrose_decision_writeback`

---

## Problem / Strategic framing

Every hyperscaler and framework is shipping an **agent runtime** — loops, tools,
memory, orchestration (Bedrock Agents, Agentforce, Copilot Studio, LangGraph,
CrewAI). That layer is commoditizing fast. Competing there means losing on
distribution to the clouds and on velocity to frameworks.

What almost none of them have is what Datacern already built: a **governance
fabric** (typed four-eyes proposals, risk tiering, anti-laundering, kill
switches, per-agent guardrails, workspace-scoped data access) and a
**tamper-evident audit chain** (hash-chained WORM). As companies deploy more
agents, the acute questions become exactly the three Datacern was built to
answer: *who approved this action, can we prove it, and is it actually
working?*

**The repositioning:** stop competing as "another agentic platform"; become
**the governed decision layer that other people's agents must pass through**. A
customer builds their own agent however they like — LangGraph, Claude, a
Copilot — but when that agent needs to act on regulated case/SoR data, it does
so ONLY through Datacern's governed tools, so every write becomes a four-eyes
proposal in the WORM chain, subject to kill switches, guardrails, and
per-resource workspace grants. This turns competitors' agent-platform teams
into Datacern's funnel: their platform team builds the bot, their risk/
compliance team mandates this layer.

## What already exists (researched, not assumed)

The entire enforcement spine is already built and already fires — for internal
agents. Confirmed seams:

- **The MCP gateway** (`tool-plane`, real MCP JSON-RPC at `POST /mcp`) runs the
  full per-call pipeline on every `tools/call`: authN → kill/enablement → OPA
  obo-grant → rate-limit → schema → tier → grant-verify → invoke → audit, and
  emits `ai.tool_invoked.v1`. A **write-tier call without a signed grant already
  returns `PROPOSAL_REQUIRED` and never executes.**
- **`ProposalService.create_from_intent`** (`agent-runtime`) is the single
  proposal-minting chokepoint: caller-permission gate (`_authorize_caller`),
  toolset allow-list + `write-proposal` tier ceiling (`_enforce_guardrail`),
  server-derived `predicted_effect` (anti-laundering), and the `ai.proposal.v1`
  WORM emit carrying `via_agent` (which agent acted) distinct from `actor` (on
  whose behalf).
- **Four-eyes** (`decide` → distinct-approver check → self-approve block →
  signed execution grant → apply) is enforced and reused by the existing
  `/inbox` approval UI.
- **The signed proposal-execution grant** (RS256, bound to tenant/tool/tier/
  args-digest) is the cryptographic guarantee that only a human-approved write
  executes; the gateway refuses forged/expired/mismatched grants.
- **The backend MCP facade** (case-service `POST /internal/v1/mcp/invoke`,
  SPIFFE-allowlisted, fail-closed, re-checks OPA independently) is the second,
  in-cluster gate on the actual SoR mutation.

**The gap** is narrow and specific: all of the above is reachable only by an
internal `typ=agent_*` principal minted by the platform, and only from inside
the cluster. There is no external, tenant-owned agent principal, no ingress
that turns an external agent's proposed write into a governed proposal, and the
`data_scope/budget/pii` guardrail slice lives inside the internal graph (so it
doesn't cover an external caller).

---

## WS1 — Governed external-intent ingress (inc-1) — the spine

### Analysis
The highest-leverage, smallest build: a customer's agent must be able to
*propose* a write and have it ride the exact same four-eyes + WORM rails as an
internal agent, with the agent's own declared toolset enforced. Everything
downstream of `create_from_intent` already delivers this — it just has no
external ingress.

### Design
A new authenticated endpoint on agent-runtime, `POST /external/v1/intents`,
that:
- authenticates the caller as an **agent principal** (`typ` starts with
  `agent`) — i.e. a registered agent identity, never a raw user;
- builds a `WriteIntent` from the request and routes it through
  `ProposalService.create_from_intent` with an **empty auto-execute policy**, so
  an external agent's write can ONLY ever become a *pending* proposal — never an
  inline write, regardless of tenant auto-execute config. This is a deliberate
  governance stance: external callers are strictly less trusted than the
  platform's own graphs, so the auto-execute fast-path is denied to them
  entirely.

All existing controls apply unchanged: the agent's `AgentVersion.toolset`
allow-list and the `write-proposal` tier ceiling (`_enforce_guardrail`), the
on-behalf-of caller-permission gate (`_authorize_caller`, which for
workspace-scoped actions already enforces workspace containment via the
per-resource RBAC grant), the server-derived effect, the `ai.proposal.v1` WORM
emit with `via_agent`, and the existing `/inbox` four-eyes decide→apply.

### Implement / Test
- [x] `POST /external/v1/intents` ingress + `Run` shell + propose-only routing —
  see Implementation & Test log below (unit + live-verified to the WORM store).

---

## WS2 — External-agent identity (self-service credential) — planned

A dedicated per-agent credential so a tenant can onboard its own agent without
the platform minting the token. `/token/agent/external` exchange (template:
`/token/embed/oidc` — already mints short-lived, workspace-scoped, per-end-user
tokens from a tenant IdP) → a `typ=agent_obo` token bound to `{tenant,
agent_key, agent_version, obo_sub?, scopes=read-toolset}`. Read tools are in the
token scope; write tools never are, so a gateway write `tools/call` still yields
`PROPOSAL_REQUIRED`. External agents register as custom agents (BRD 53) with an
`origin=external` marker.

## WS3 — Public governed edge — planned

Expose the read/list-tools + propose surface at the one public ingress
(bff-graphql), forwarding to the internal gateway/ingress with the external
token. No change to the gateway pipeline itself.

## WS4 — Guardrail lift (data_scope / budget / PII) — planned

Lift the `data_scope refusal / budget cap / PII-egress redaction` envelope out
of the internal `persona_copilot` graph to a request-scoped enforcement point
that also covers the external-intent ingress, closing the one control that does
not automatically transfer to external callers today.

## WS5 — SDK + compliance-evidence export

The tangible artifacts that make the differentiation demoable: an auditor-facing
evidence export ("here is the tamper-evident audit pack for this decision") and
a thin client SDK (the customer's agent calls "propose(tool, args)").

### Implement / Test
- [x] auditor evidence-pack endpoint (audit-service) — see Implementation & Test
  log; live-verified through the real OPA auth path against the running WORM store.
- [x] BFF query + ui-web "view/download evidence pack" on the decision detail —
  live-verified in the browser end to end.
- [x] thin client SDK (propose/list-tools helpers) — `sdk/agent-python`,
  live-verified against the running ingress.

---

## Sequencing
WS1 (spine) first — it proves the whole external-write→four-eyes→WORM thesis
with the smallest build. WS2/WS3 (identity + public edge) make it a real
self-service product surface. WS4 closes the last guardrail gap. WS5 is the
go-to-market polish. Each is independently shippable.

---

## Implementation & Test log (landed increments)

### WS1 — governed external-intent ingress — DONE

**Research before building** (a dedicated read-only survey, not assumed from
the strategic framing): confirmed the entire enforcement spine already exists
and already fires for internal agents — the MCP gateway's per-call pipeline, the
`ProposalService.create_from_intent` chokepoint (caller-gate + toolset/tier
`_enforce_guardrail` + server-derived effect + `ai.proposal.v1` WORM emit), the
four-eyes `decide`, the signed execution grant, and the SPIFFE-fail-closed
backend facade. The gap was narrow: no external agent principal, no ingress
turning an external agent's proposed write into a governed proposal.

**Implementation:** one new route, `POST /external/v1/intents`
(`services/agent-runtime/app/api/routes/external.py`), wired into `main.py`.
It authenticates the caller as an **agent principal** (`typ` starts with
`agent` — a registered agent identity, never a raw user), builds a
`WriteIntent`, persists a lightweight `Run` shell (no graph session — runs has
no FK to sessions), and routes through `ProposalService.create_from_intent`
with an **empty auto-execute policy** so an external agent's write can ONLY ever
become a *pending* proposal, never an inline write, regardless of tenant
config. Zero change to any downstream control: the agent's `AgentVersion.toolset`
allow-list + `write-proposal` tier ceiling, the on-behalf-of caller-gate, the
anti-laundering `derive_effect`, the `ai.proposal.v1` WORM emit with
`via_agent`, and the existing `/inbox` four-eyes decide→apply all apply unchanged.

**Test:** `tests/unit/test_external_intents.py` — 7 tests driving the REAL route
through the REAL `ProposalService` (in-memory container, no mocks): a valid
intent becomes a pending proposal with `via_agent` attribution and a
server-derived (agent-claim-demoted) effect; a tool off the agent's registered
allow-list → `GUARDRAIL_VIOLATION`; `write-direct` tier → `GUARDRAIL_VIOLATION`
(external agents can never get a direct write); a raw user token → 403; a
high-risk external proposal cannot be self-approved by its own on-behalf-of user
(four-eyes binds external proposals exactly as internal ones); body validation.
Full agent-runtime unit suite: 296 passed (up from 289).

**Live-verified end to end** against the real running stack (user-approved
agent-runtime restart): minted a real RS256 agent token with the harness signing
key (kid `e2e-harness-key-1`, the same key identity-service's OBO exchange signs
with, so it verifies against the real JWKS), POSTed a `write-proposal` intent →
`200`, `status: pending`, `executed: false`. Confirmed the row landed in **real
Postgres** (`agent_runtime.proposals`: `acme-ext-bot`/`write-proposal`/`pending`/
`obo_user=u-ext-smoke`); confirmed the `ai.proposal.v1` / `proposal.created`
event was emitted with `via_agent={acme-ext-bot,1}` distinct from `actor`; and
confirmed it reached the **WORM store** (ClickHouse `audit_events`,
`event_type=proposal.created`, `via_agent_id=acme-ext-bot`). A live
`write-direct` attempt was refused with `403 GUARDRAIL_VIOLATION`, and the
agent's own `predicted_effect.summary` ("external claimed effect") was demoted
to `agent_summary` while the server derived the `authoritative_summary` +
`args_digest` + `risk` — anti-laundering working live for an external caller.

**Known limitation, flagged not hidden (WS4):** the `data_scope/budget/pii`
guardrail slice is enforced inside the internal `persona_copilot` graph, which
an external caller bypasses; the toolset + tier ceiling + on-behalf-of caller-
gate (which for workspace-scoped actions already enforces workspace containment
via the per-resource RBAC grant) DO bind external agents today. Lifting the
data_scope/budget/PII envelope to the ingress is WS4.

_Next: WS2 (dedicated external-agent self-service credential + `/token/agent/
external` exchange) — gated on the next explicit go-ahead._

### WS5 — auditor evidence-pack (audit-service) — DONE

**The demoable differentiator: "here is the tamper-evident audit pack for this
decision."** Given one governed decision (`proposal_id`), audit-service now
assembles an auditor-facing evidence pack — everything an examiner needs to see
who proposed, who approved (a DISTINCT human), when, the exact governed tool
call, and cryptographic proof the record wasn't altered.

**Research before building** confirmed the entire supply already exists and just
needed composing (no async/zip machinery for a single small decision): resource/
trace-scoped `chstore.Search`, per-day `ChainScan` + `chain.Verify`, the sealed-
manifest proof (`pgstore.GetChainHead.SealedAt` + `LatestManifest`), and the
proposal↔tool join recipe already used by the existing `AIDecisionLog` pack.

**Implementation** (`services/audit-service`): new `internal/compliance/
evidence.go` — `Builder.EvidencePack(tenant, proposalID)` gathers the proposal
lifecycle (exact `wr:{tenant}:agent:proposal/{id}` URN) + the executed
`ai.tool_invoked.v1` calls (shared `trace_id`), embeds each event's immutable
chain position, and per distinct chain-day re-verifies the hash chain against
its sealed WORM manifest. A pure `summarizeDecision` derives the four-eyes claim
(`four_eyes = a distinct human approver != the on-behalf-of user`). New
synchronous `POST /compliance/evidence-pack` returns the pack inline (the pack
is small — one decision). Reuses the already-registered `audit.compliance.read`
OPA action (deliberately, to sidestep the recurring rbac-catalog-gap bug class).
`compliance.Builder` gained an optional `PG` (nil-safe; the SOC2/AI-decision-log
packs don't need it).

**Test:** 4 pure unit tests for `summarizeDecision` (four-eyes true; self-
approval is NOT four-eyes; rejected outcome; autonomous-no-approver). A
Docker-backed integration test seeds a real 3-event decision (agent-proposed
on-behalf-of u-alice → tool executed → approved by a DISTINCT human u-bob) into
real ClickHouse with a correctly-computed hash chain, seals the day, and asserts
the pack proves `four_eyes=true` AND `sealed/valid/manifest_match` end to end;
plus an unknown-proposal → NOT_FOUND. Full audit-service unit suite green.

**Live-verified end to end** against the running stack (user-approved audit-
service restart): minted a real `typ=service` token scoped `audit.compliance.
read`, POSTed the external-agent proposal from WS1 inc-1 → `200` with the pack
assembled from the **real running WORM store** (real `chain_seq: 407`, real
`chain_hash`, `via_agent: acme-ext-bot`), and — critically — the chain-proof for
today's still-unsealed day honestly reported `sealed: false` / "verifiable once
the daily WORM export seals it" rather than fabricating a verification. A token
WITHOUT the scope → `403` (the OPA gate bites); an unknown proposal → `404` (no
fabricated empty pack).

### WS5 — BFF + UI evidence-pack surface — DONE

**BFF:** `AuditClient.evidencePack(proposalId)` + `EvidencePackDTO` (mirrors the
Go wire shape), a fully-modeled GraphQL `EvidencePack`/`EvidenceDecision`/
`EvidenceEvent`/`EvidenceDayProof` type set, `mapEvidencePack` (snake→camel),
and a JWT-passthrough `evidencePack(proposalId)` query resolver. Typecheck +
lint clean, `schema.graphql` snapshot regenerated (scoped to the evidence
additions), full bff suite 296 tests green.

**UI:** new `EvidencePackPanel` on `ProposalDetail` — a lazy "View evidence
pack" that fetches only on click (`useEvidencePack`, `enabled`-gated), then
renders the four-eyes claim as a prominent green/amber badge, the
proposer→approver + tool call, a per-chain-day tamper-evidence list (re-verified
vs. the sealed manifest, or the honest "not sealed yet" note), and a client-side
"Download JSON" of the pack. Typecheck + lint clean of new issues, full ui-web
suite 479 tests green.

**Live-verified end to end in the browser** (user-approved bff-graphql restart;
ui-web hot-reloaded): logged in as Admin, opened the WS1 external agent's pending
proposal in the Approval inbox, clicked "View evidence pack" → the panel rendered
from the **real UI→BFF→audit-service path** (the Admin's own JWT, not a service
token): the amber "No distinct human approval recorded yet" badge (correct — the
proposal is pending), "Proposed by acme-ext-bot@1 (autonomous)", and a
"TAMPER-EVIDENCE · PENDING SEAL" panel honestly showing today's still-unsealed
day rather than faking a verification. Zero console errors. The same query
through the BFF directly returns the mapped camelCase pack with the real
`chainSeq: 407`. The four-eyes/sealed happy path stays covered by the
audit-service integration test (a real sealed, approved decision).

### WS5 — thin client SDK — DONE

`sdk/agent-python/` — a **dependency-free (stdlib-only) Python SDK** a customer
drops into their own agent. Rule-7 thin: one `DatacernAgentClient` with
`propose(...)` (wraps `POST /external/v1/intents`) and `list_tools(gateway_url)`
(real MCP `tools/list`), a `Proposal` dataclass, and a typed `DatacernAgentError`
carrying the platform error envelope (`code`/`message`/`trace_id`). The HTTP
transport is injectable so the request-building + response-parsing are
contract-testable without a socket. Includes a `pyproject.toml` (zero runtime
deps) and a customer-facing README.

**Test:** 7 contract tests (`tests/test_client.py`) with a recording in-process
transport — request URL/method/auth-header/body shape, propose-only defaults
(`tier=write-proposal`, `side_effects=reversible`), optional-field passthrough,
client-side fail-fast validation (empty `affected_urns` never hits the wire),
the typed error-envelope path, and `list_tools` MCP JSON-RPC. All pass.

**Live-verified against the running agent-runtime:** `agent.propose(...)`
created a **real** governed pending proposal (`98a8d192-…`) with the
**server-derived** `predicted_effect` (anti-laundering `authoritative_summary` +
`args_digest`) — proof the write went through the real `ProposalService`, not a
stub. Proposing above the tier ceiling (`tier=write-direct`) was refused with a
real `403 GUARDRAIL_VIOLATION` surfaced as a `DatacernAgentError` — the SDK's
error path works against the live enforcement, and the tier ceiling genuinely
binds an external agent.

**Honest finding (registration gap, not an SDK/enforcement bug):** proposing a
tool NOT on the agent's allow-list was *accepted* live, because the WS1 demo
agent `acme-ext-bot` was registered with an EMPTY declared toolset —
`_enforce_guardrail`'s allow-list check is `if allowed and tool not in allowed`,
so an empty toolset legitimately means "no allow-list declared" and the check is
skipped. The enforcement code is correct; the agent config just lacked a
toolset. All the other controls still held (propose-only, four-eyes, the tier
ceiling). **Follow-up:** register external agents with an explicit toolset so the
allow-list binds — a WS1/registration concern, tracked for the next increment.

### WS2 — self-service external-agent credential + token exchange — DONE

Until now a customer's agent could only obtain an `agent_autonomous` token from a
harness-signed credential — fine for a demo, not a self-service product. WS2
lets a **tenant admin mint the credential itself** and the customer's agent
exchange it, with no dependency on the agent-registry sync.

**identity-service (backend):**
- Migration `0009_external_agent_keys` — a platform-scoped table (`external_agent_keys`,
  no RLS, `tenant_id` column scopes admin list/revoke) modelled on the
  service-account key: only the argon2 hash is stored, plaintext shown once.
- Domain `ExternalAgentKey` + `wr_xa_<id>.<secret>` credential format
  (`Format`/`Parse`/`NewExternalAgentKey`, reusing `NewAPIKeySecret`/`HashSecret`).
- `Store` port + memory + postgres implementations (create/get/list/revoke/touch).
- `TokenService.ExternalAgentExchange` — parse → lookup by id → `Active` +
  `VerifySecret` → tenant issuable → mint an `agent_autonomous` token carrying the
  key's `agent_id@version` + declared scopes → `TouchExternalAgentKey` → emit a
  `security.external_agent_token_issued` audit event. A suspended tenant is denied
  and audited; the exchange fails **closed** on every bad-credential edge.
- Routes: `POST /token/agent/external` (unauth edge — the key IS the credential,
  like `/token/embed`); admin CRUD `GET|POST /tenants/self/external-agents` +
  `DELETE /tenants/self/external-agents/{id}`, all gated on `identity.user.admin`
  and self-scoped on the caller's tenant claim. Create returns the plaintext once.

The minted token is still just an identity + scopes — WS1's ingress forces every
external write through propose-only + four-eyes + the write-proposal tier ceiling
regardless, so a self-minted credential can never bypass the proposal rails.

**Test:** 5 fixture-harness tests (`handlers_external_agent_test.go`) exercise the
real HTTP handlers + real store + real argon2 + real JWT issuer end-to-end:
mint-requires-admin-scope (zero-scope member → 403), exchange-mints-a-Bearer-token,
malformed/unknown keys all fail closed 401 (never 500, never a token),
revoke-then-exchange-fails (revoked key → 401), and cross-tenant revoke isolation
(tenant B's admin gets 404 revoking tenant A's key, and A's key keeps working).
The listing never serializes `secret_hash`. Full identity-service unit suite green,
`go vet` clean. Postgres store verified by column-alignment inspection (memory tier
is the tested path; both share the domain contract).

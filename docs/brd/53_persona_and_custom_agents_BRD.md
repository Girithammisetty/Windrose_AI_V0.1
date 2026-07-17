# BRD 53 — Persona Agents, Tenant Custom Agents & the Guardrail Envelope

**Deliverable type:** Core capability (agent-runtime + rbac + tool-plane + ai-gateway + ui-web)
**Publisher:** Windrose · **Initial version:** 1.0.0 · **Status:** authored; increment 1 in build
**Extends:** BRD 14 (agent-runtime), BRD 13 (tool-plane), BRD 02 (rbac), BRD 12 (ai-gateway), BRD 52 (ml-engineer agent — the risk-tiered-autonomy precedent).

---

## 1. Overview

**Purpose.** Three capabilities delivered on one governance spine:
1. **Persona agents** — every default persona a pack ships (analyst, investigator, specialist, approver, auditor) gets a *role-grounded* copilot: an agent whose instructions, allowed tools, and data scope are bound to what that persona is permitted to do.
2. **Tenant custom agents** — a tenant admin can introduce their OWN agents **as governed configuration, never as code**: name, persona/role binding, prompt, an allow-list of existing governed tools, a data scope, and an autonomy ceiling. The custom agent runs on a **shared, safe, platform-owned graph template** — tenants supply intent and constraints, not executable behavior.
3. **The guardrail envelope** — every agent (fixed, persona, or custom) runs inside an explicit, per-agent, machine-enforced policy: which tools it may call, which data/workspaces it may read, the maximum autonomy tier it may reach, its spend cap, and its PII posture. Defense-in-depth: the envelope is validated at author time, enforced at runtime in agent-runtime, and re-enforced independently at tool-plane and ai-gateway.

**Why.** The platform's differentiator is *governed* AI decisions. "Let tenants build their own agents" is the natural expansion — but done naively it is the single largest way to destroy the trust the platform sells. The safe form is the same insight that made the ml-engineer agent safe (BRD 52): **autonomy is a property of a governed TOOL and an explicit POLICY, not of agent-supplied code or prompt goodwill.** No tenant code executes; no agent exceeds its declared envelope; every consequential action is a human-approved proposal.

**The security thesis (non-negotiable).** A tenant custom agent is a *declaration* — `{persona, prompt, allowed_tools ⊆ tenant-enabled tools, data_scope ⊆ tenant's own, max_tier ≤ write-proposal, budget}` — bound to a shared graph the platform wrote and audited. It can do strictly less than the human who invokes it (caller-gate), strictly less than its declared allow-list (agent-scoped tool enforcement — the one genuinely new enforcement point), and never more than proposal-mode without a distinct human approving each consequential act.

**In scope.** Persona↔agent binding; a config-driven "persona-copilot" graph; the per-agent guardrail-policy schema + validation; agent-scoped tool-allowlist enforcement at runtime (defense-in-depth with tool-plane); tenant self-service create/update/enable/disable/kill of custom agents; per-agent budget; UI to author and govern them; audit of every custom-agent action.

**Out of scope (never).** Tenant-supplied code or arbitrary graph topology; agents that self-mint credentials or connections; autonomy above write-proposal for any custom agent (destructive/admin tools stay operator-fixed-agent only); cross-tenant agent sharing; agents bypassing four-eyes, caller-gate, RLS, or budgets.

## 2. Actors & user stories

**Personas:** Tenant Admin / Agent Author (TA), each default persona as an agent *user* (PU), Model/Decision Approver (MA), Compliance Auditor (CA), Platform Operator (OP), the guardrail engine (ENGINE).

- **US-1** As a PU (e.g. Dispute Intake Analyst), my copilot is grounded in MY role: it proposes only dispositions I'm allowed to make, cites only data I can see, and never offers an action outside my permissions.
- **US-2** As a TA, I create a custom agent from a form: name, which persona it serves, a prompt, a checklist of governed tools it may use (only tools already enabled for my tenant appear), the workspaces/datasets it may read, and an autonomy ceiling (read-only or propose) — with **no code**.
- **US-3** As a TA, when I save it the platform validates the envelope: it rejects any tool I haven't enabled, any autonomy above propose, any data scope outside my tenant — with a specific reason.
- **US-4** As a PU using a custom agent, it can never take an action its allow-list omits or that I personally couldn't take (caller-gate); consequential actions arrive as proposals for four-eyes approval.
- **US-5** As an MA, custom-agent proposals are indistinguishable in governance from fixed-agent proposals: evidence-carrying, four-eyes, no self-approval, fully audited.
- **US-6** As a TA, I can disable or kill-switch any custom agent instantly, and cap its LLM spend; a killed agent stops accepting sessions immediately.
- **US-7** As a CA, for any agent action I can reconstruct: which agent, its exact envelope at the time, the invoking human, every tool call, the proposal, and the approver — and I can prove no action ever exceeded the envelope.
- **US-8** As an OP, I can set platform ceilings that no tenant envelope may exceed (e.g. custom agents may never reach write-direct/destructive/admin tiers, regardless of tenant config).
- **US-9** As ENGINE, I refuse — at author time AND at runtime AND at tool-plane — any tool call outside the agent's allow-list, any read outside its data scope, any autonomy above its ceiling, and any call over budget; refusals are logged, never silent.

## 3. Functional requirements

### PA-FR-001 — Guardrail policy schema
Every agent version carries an explicit `guardrail_policy`:
`{ allowed_tools: [tool_id], max_tier: read|write-proposal (never write-direct/admin for custom), data_scope: {workspaces?, dataset_urns?, semantic_models?}, budget: {max_tokens_per_session, ai-gateway scope ref}, pii: {block_pii_egress: bool, redact: bool}, autonomy: read_only|proposal }`. Fixed agents get a default policy = their existing toolset + declared write_mode; custom agents get the tenant-authored one, clamped to platform ceilings.

### PA-FR-010 — Persona binding
An agent may declare `persona` = an rbac role/permission-group label. The persona-copilot graph grounds its system prompt in that persona's allowed actions and refuses to propose actions outside them (role-grounded — closes the "copilot not role-grounded" gap). Packs bind their shipped roles to persona copilots via TenantAgentConfig.

### PA-FR-020 — Config-driven persona-copilot graph
One shared, platform-owned LangGraph template (`persona_copilot.v1`) parameterized entirely by config (persona, prompt, allowed_tools, data_scope): ground (read only within scope) → reason (LLM, prompt-injection-hardened, output-validated) → propose (only allow-listed tools, tier-capped). No tenant code; the graph is the same audited path for every custom agent.

### PA-FR-030 — Agent-scoped tool enforcement (the new enforcement point)
At runtime, before a WriteIntent becomes a proposal, agent-runtime MUST verify the intent's `tool_id ∈ agent.guardrail_policy.allowed_tools` and `intent.tier ≤ max_tier`; a violation fails closed (no proposal, audited). tool-plane independently re-checks tenant-enablement + tier (defense-in-depth); ai-gateway independently enforces budget. Three layers, no single point of trust.

### PA-FR-040 — Data-scope enforcement
Grounding reads issued by a custom agent are constrained to its `data_scope` (workspaces/datasets/models) on top of RLS — an agent scoped to workspace W cannot read workspace V even if the invoking human could. Out-of-scope reads return empty + a logged refusal.

### PA-FR-050 — Tenant self-service lifecycle
Tenant admins (rbac `ai.agent.admin`) can create/update/enable/disable/kill custom agents and set budgets via API + UI. Custom agents are tenant-scoped (RLS), never visible or runnable cross-tenant. Operator-only actions (new FIXED code agents, platform ceilings) stay gated on operator scope.

### PA-FR-060 — Author-time validation
Saving an envelope validates: every allowed_tool is tenant-enabled; max_tier ≤ platform ceiling (write-proposal); data_scope ⊆ tenant; budget ≤ tenant budget; persona is a real tenant role. Structured rejection per PKG-FR-001 with the offending field.

### PA-FR-070 — Audit & reconstruction
Every custom-agent run records the agent id, the envelope digest in force, invoking human, tool calls, proposal, approver, and any refusals — sufficient for US-7 full reconstruction.

### PA-FR-080 — Kill & budget
Per-agent kill-switch (RedisKillRegistry scope=agent_version_tenant) and per-agent ai-gateway budget scope. A killed or over-budget agent refuses new sessions cleanly (absent, not erroring).

## 4. Business rules

- **BR-1** No tenant code executes — custom agents are config over the shared safe graph; arbitrary graph topology is impossible by construction.
- **BR-2** A custom agent's authority ⊆ min(its allow-list, the invoking human's permissions, the platform ceiling). Never a superset of any of the three.
- **BR-3** Custom agents cap at write-proposal — destructive/admin/write-direct tiers are operator-fixed-agent only. Consequential acts are always human-approved proposals (four-eyes, no self-approval, caller-gate — inherited).
- **BR-4** Deterministic guardrails over prompt guardrails: allow-list, tier cap, data scope, and budget are code-enforced at three independent layers; the prompt is never the security boundary.
- **BR-5** Fail closed + never silent: any envelope violation blocks the action and is audited.
- **BR-6** Envelope is versioned and captured per run — an approver/auditor always sees the exact policy that was in force.
- **BR-7** RLS + data-scope are additive constraints, never relaxations — a custom agent can only ever see a subset of its tenant's data.
- **BR-8** Platform ceilings override tenant config — no tenant setting can raise autonomy, budget, or tier above the operator-set maximum.

## 5. NFRs (deltas)

| Metric | Target |
|---|---|
| Author-time envelope validation | 100% of violations rejected with the offending field |
| Runtime out-of-allowlist tool call | 0 reach tool-plane (blocked in agent-runtime) + 0 execute (blocked at tool-plane) |
| Cross-scope data read by a scoped agent | 0 |
| Custom-agent action without audit record | 0 |
| Over-budget agent LLM call | 0 (ai-gateway 429/degraded) |

## 6. Acceptance criteria

- **AC-1** A tenant admin creates a custom agent via API/UI with `{persona, prompt, allowed_tools, data_scope, max_tier=proposal, budget}`; it publishes tenant-scoped and is runnable only in that tenant.
- **AC-2** Author-time validation rejects: a non-enabled tool, max_tier>write-proposal, data_scope outside tenant, budget over cap — each with the offending field.
- **AC-3** The custom agent runs on the shared persona-copilot graph and produces a four-eyes proposal for an allow-listed tool; approval executes it; rejection changes nothing.
- **AC-4** Runtime negative: the agent cannot emit a proposal for a tool outside its allow-list (blocked in agent-runtime, audited) — and if forced, tool-plane independently blocks it.
- **AC-5** Data-scope negative: an agent scoped to workspace W returns empty for a workspace-V read even when the invoking human could see V; refusal audited.
- **AC-6** Caller-gate negative: a user lacking the underlying action cannot have the agent propose it.
- **AC-7** Kill-switch: killing the custom agent stops new sessions immediately; budget exhaustion degrades cleanly.
- **AC-8** Full audit reconstruction (US-7) demonstrated, including the envelope digest in force.
- **AC-9** Platform ceiling: a tenant config attempting write-direct/destructive is rejected regardless of tenant settings.
- **AC-10** Zero pack changes required; packs opt in by binding roles to persona copilots via TenantAgentConfig.

## 7. Rollout (phased — honest scope)

- **Increment 1 (this build):** the guardrail-policy schema + author-time validation + the config-driven persona-copilot graph + agent-scoped tool-allowlist & tier enforcement at runtime + tenant create/enable/kill API + RBAC; unit + contract tested; ONE tenant custom agent live-verified producing a four-eyes proposal, plus the AC-4/AC-6 negatives.
- **Increment 2:** data-scope enforcement (PA-FR-040) hardening, per-agent ai-gateway budget scope, PII-egress guard, and the author UI.
- **Increment 3:** persona auto-binding for all pack roles, prompt-injection/output-guard hardening, platform-ceiling operator console, and the guardrail-service extraction (moving policy eval to a dedicated service if scale warrants).

## 8. Dependencies

agent-runtime (graph, registry, proposals, kill), rbac (`ai.agent.admin`, persona roles, caller-gate), tool-plane (tenant-enablement, tier, kill — defense-in-depth), ai-gateway (per-agent budget), ui-web (author + govern surfaces), audit.

## 9. Out of scope / future

Tenant code/plugins; marketplace of shared agents; autonomy above proposal; connection/credential creation by agents; cross-tenant agents; a standalone guardrail-service (extracted only if increment-3 scale demands).

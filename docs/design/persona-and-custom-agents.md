# Design — Persona & Tenant Custom Agents + Guardrail Envelope (BRD 53)

Grounded in on-disk contracts. Companion to `docs/brd/53_persona_and_custom_agents_BRD.md`.

## What already exists (reuse, don't rebuild)
- Auto-execute policy matrix, destructive/admin never auto — `app/domain/policy.py`
  (`validate_auto_policy`, `is_auto_execute`).
- PII + prompt-injection guard on EVERY LLM call — ai-gateway `GuardrailEngine`
  (agent-runtime LLM path routes through it). So injection/PII is already guarded
  at the gateway for any graph, including a new config-driven one.
- Proposal machinery: caller-gate (`_authorize_caller`), four-eyes, no
  self-approval, first-wins, signed grants — `app/proposals/service.py`.
- `AgentVersion.toolset` (declared) + `AgentVersion.guardrail_profile` (field
  exists, unused) — `app/domain/entities.py`.
- `store.get_agent_version(key, version)`, `create_agent_version`,
  `upsert_agent_definition`; registry API `app/api/routes/registry.py`.
- Persona tone-shaping (relevance-only) — `app/graphs/persona.py`.

## The gaps this closes (increment 1)

### G1 — Agent-scoped tool-allowlist enforcement at runtime (LOAD-BEARING)
Today `AgentVersion.toolset` is declared but NEVER checked: a graph can emit a
WriteIntent for any tool, and the minted OBO token scopes = `[tool_id]` so
tool-plane's toolset gate is self-satisfied. **Fix at the authoritative
chokepoint** `ProposalService.create_from_intent` (before the proposal row is
created): fetch the run's `AgentVersion`, and if its `toolset` is non-empty,
reject any `intent.tool_id` not in it — fail closed, audited, no proposal. This
activates the declared contract for EVERY agent (fixed + custom), a genuine
platform hardening, and is what makes a custom agent's allow-list real.
Enforcement is additive to (not a replacement for) the caller-gate + tool-plane
tenant-enablement + signed grant — defense-in-depth.

### G2 — Config-driven persona-copilot graph (the shared safe template)
New `app/graphs/persona_copilot.py`, registered `persona_copilot.v1`. Fully
parameterized by `prompt_params`: `{persona, system_prompt, allowed_tools,
propose_tool, disposition/args hints, read_scope}`. Topology (fixed, platform-
owned): `ground` (read-only within scope — case/dataset reader) → `reason`
(ai-gateway LLM, already injection/PII-guarded) → `propose` (emits a WriteIntent
ONLY for `propose_tool`, which MUST be in `allowed_tools`; tier ≤ write-proposal;
required_action set so the caller-gate applies). If the config names no
propose_tool the agent is read-only (answer, no intent). No tenant code; the
same audited graph backs every custom agent.

### G3 — Tenant custom-agent create path (config, not code)
New tenant-facing route `POST /api/v1/registry/tenants/self/agents` (rbac
`ai.agent.admin`) that creates a tenant-scoped agent definition + published v1
whose `graph_ref` is FORCED to `persona_copilot.v1` (a tenant may NEVER name
another graph_ref — the only value accepted), with `toolset` = the validated
allow-list and `prompt_params` carrying persona/system_prompt/propose_tool.
Validation (PA-FR-060): graph_ref is persona_copilot.v1; every allowed_tool is
tenant-enabled (checked against tool-plane); tier ≤ write-proposal; persona is a
real tenant role. Definitions carry `owner_tenant` so RLS + the registry list
scope them to the authoring tenant only.

## Enforcement layers (defense-in-depth, per BR-4)
1. **Author time** — validation route rejects a bad envelope with the field.
2. **agent-runtime** — G1 chokepoint: tool_id ∈ toolset, tier cap; caller-gate.
3. **tool-plane** — tenant-enablement + published + not-killed + signed grant.
4. **ai-gateway** — budget + injection/PII on the LLM call.
No single layer is the boundary.

## Increment boundaries (honest)
- **Inc 1 (build now):** G1 + G2 + G3 + validation + RBAC; unit + contract
  tests; live-verify a tenant custom agent → four-eyes proposal, plus the
  out-of-allowlist NEGATIVE (G1 blocks it) and caller-gate negative.
- **Inc 2:** data-scope read enforcement hardening, per-agent ai-gateway budget
  scope, PII-egress block mode, author UI (admin/agents form).
- **Inc 3:** persona auto-binding for all pack roles, operator platform-ceiling
  console, optional guardrail-service extraction.

## Test plan
- Unit (agent-runtime, fake deps): persona_copilot proposes ONLY the configured
  allow-listed tool; read-only config emits no intent; injection-y prompt still
  produces a schema-valid proposal (guard is at gateway).
- Unit (proposals): `create_from_intent` rejects a tool_id outside the agent
  version's toolset (fail closed, no proposal) — the G1 negative — and allows an
  in-toolset one.
- Contract (registry): tenant create rejects non-persona_copilot graph_ref, a
  non-enabled tool, tier>write-proposal; accepts a valid envelope and publishes
  tenant-scoped.
- Live e2e: create a `wr-disputes` custom agent (persona = Dispute Intake
  Analyst, allowed_tools=[case.apply_disposition], propose a disposition), run
  it, get a four-eyes proposal in the inbox; then a forced out-of-allowlist
  intent is blocked.

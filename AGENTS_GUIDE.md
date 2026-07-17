# Windrose Agents — How They Work & Where to See Them

**Date:** 2026-07-12 · **Audience:** developers debugging or verifying agent behavior.
**Companion docs:** `WALKTHROUGH.md` (persona walkthrough) · `BUILD_STATUS.md` (implementation status) · `../docs/brd/14_agent_runtime_BRD.md` (specification) · `../docs/brd/13_tool_plane_BRD.md` (tool authority chain) · `../docs/brd/12_ai_gateway_BRD.md` (LLM routing).

**The problem this doc solves.** You saw a "copilot" mentioned across the UI, watched some agent activity, but couldn't tell what agent ran, what it saw, what LLM call it made, what tool it invoked, what it proposed, and where any of that surfaced. This is the map.

---

## 1. The mental model — five moving parts

An agent in Windrose is not "an LLM in a loop." It's a **governed compound object** with five parts:

```
                                 ┌── ai-gateway (LLM calls, budgets, cascade)
                                 │
   session ─→ run ─→ LangGraph ──┼── tool-plane (MCP registry + write authz)
                    (nodes)      │
                                 └── memory-service (RAG + task memory)

           ↓                              ↓ (on write intent)
       trace tree                      proposal
       (visible)                       (queued for human)

           ↓                              ↓ (approved)
       Kafka event                    signed grant → tool executes → domain service (case/dataset/etc.)
                                                                              ↓
                                                                          audit event
```

Every part has a specific place you can watch it happen. The rest of this doc is that map.

## 2. The eight agents in the catalog

Seeded in `services/agent-runtime/app/agents/catalog.py`. Three have real published v1 graphs (case-triage, governance, analytics — the priority agents). The other five have graph files (`services/agent-runtime/app/graphs/`) but are staged as draft or lower-priority definitions until their real graphs finalize.

| Key | Display name | Write mode | Graph | Primary tool | What it does |
|---|---|---|---|---|---|
| `case-triage` | Case Triage Copilot | **proposal** | `triage.v1` ✅ published | `case.apply_disposition` | Proposes claim dispositions (severity/assignee/disposition) grounded in case data + resolved-case RAG. Every seeded PENDING inbox proposal came from this agent. |
| `governance` | Governance Agent | **proposal** | `governance.v1` ✅ published | `mlops.open_retrain` | Watches drift + correction signals, opens retrain proposals when thresholds breach. The learning-loop closer. |
| `analytics` | Analytics Agent | **read_only** | `analytics.v1` ✅ published | (read tools only via semantic-service) | Conversational analytics over governed semantic layer. Never proposes writes. |
| `onboarding` | Onboarding Agent | proposal | `onboarding.v1` | `ingestion.create` | Proposes ingestion configs + column mappings for new data sources. |
| `dashboard-designer` | Dashboard Designer | proposal | `dashboard_designer.v1` | `chart.dashboard.create` | Proposes draft dashboards + charts grounded in the semantic layer. |
| `model-training` | Model Training Agent | proposal | `model_training.v1` | `pipeline.template.create_from_algorithm` | Proposes training runs — fills algorithm template + hyperparameters. |
| `inference` | Inference Agent | proposal | `inference.v1` | `inference.submit` | Proposes batch inference jobs grounded in model version + input-schema compatibility. |
| `meta-router` | Meta Router | proposal | `meta_router.v1` | (routing only) | Classifies free-text requests and delegates to the specialist agent that owns the matching skill. |

**Write mode matters.** `proposal` agents CANNOT execute their intended write directly — every write becomes a `Proposal` row that a human decides on. `read_only` agents (analytics) have no write tool at all. This is enforced by tool-plane, not by convention: the write tool refuses the call unless it sees a signed proposal-execution grant that agent-runtime only issues after human approval.

## 3. Lifecycle of a run — what happens in order

Take the seeded triage-copilot proposal on `CLM-1001` as the example.

```
1. TRIGGER
   ├── User asks copilot from case detail — the drawer POSTs to agent-runtime /sessions/{id}/messages
   ├── Or case-service emits case.created — governance agent listens via Kafka
   └── Or the seed_claims_demo.py script POSTs /runs to create a fresh triage run

2. SESSION + RUN CREATED (agent-runtime)
   ├── Postgres row in sessions + runs tables (per BRD 14 ART-FR schema)
   ├── Temporal AgentRunWorkflow scheduled (durable retries + HITL signals + timers)
   └── OTel span "invoke_agent" starts

3. GRAPH EXECUTES (LangGraph in Temporal activity)
   Each node in the graph is one of:
   ├── llm_call   → ai-gateway POST /v1/chat/completions (real Ollama)
   ├── tool_call  → tool-plane POST /tools/invoke (real MCP)
   ├── memory     → memory-service similarity retrieval
   ├── decision   → deterministic node (e.g., risk classifier)
   └── propose    → writes a Proposal row (does NOT execute)

   Every node writes a trace span with start_at, duration_ms, tokens, cost.
   Streaming tokens go to realtime-hub SSE topic ai.agent_run.<run_id>.

4. PROPOSAL EMITTED (if graph reached propose node)
   ├── Postgres row in proposals table
   ├── Kafka event proposal.created on agent.events.v1
   ├── Inbox badge increments via realtime-hub SSE
   └── run.status transitions to WAITING_HITL

5. HUMAN DECIDES (from ui-web /inbox)
   ├── Approve → agent-runtime issues signed grant (JWKS-published RSA key)
   ├── Reject → run resumes with rejection reason (feeds eval data)
   └── Edit args → new proposal spawned with edited args diff highlighted

6. GRANT VERIFIED + WRITE EXECUTED (tool-plane)
   ├── tool-plane verifies signed grant against agent-runtime JWKS
   ├── Tool endpoint (case-service /cases/{id}/disposition) receives the write
   ├── case-service applies disposition, emits case.disposition_applied Kafka event
   └── Run trace records tool_call node completion

7. AUDIT
   ├── audit-service consumes agent.events.v1 and tool.events.v1
   ├── Every step (session, run, tool call, proposal, decision, disposition) becomes an
       audit row with dual attribution: actor={type:'user',id} + via_agent={agent_id,version}
   └── Available at /admin/audit
```

Every step is visible somewhere — see §4.

## 4. Where to SEE agents in the UI — URL map

### 4.1 The chat surfaces

| Where | URL | What you see | Backing API |
|---|---|---|---|
| Full-page copilot | `/copilot` | Thread with the assistant. Streaming tokens (real Ollama). Citations rendered as `<UrnLink>`. Suggested actions as buttons that deep-link (mostly to `/inbox` for a proposal the assistant just spawned). Persistent AI label (EU AI Act Art. 50). | agent-runtime `POST /sessions/{id}/messages` (SSE streaming) |
| Copilot drawer | Right side of every module page | Same thread engine as `/copilot`, context = current URN + route metadata. Opening from a case sends `wr:<tenant>:case:case/CLM-1001` as first-turn context. | Same as above |
| Case detail Proposals tab | `/cases/CLM-1001` → Proposals tab | Any proposals the triage agent has spawned for this case, historical + pending. | bff-graphql `Case.proposals` |

### 4.2 The visibility surfaces (what everyone misses)

**This is where "what is the agent doing?" gets answered.**

| Where | URL | What you see | Backing API |
|---|---|---|---|
| **Agent runs history** | `/copilot/runs` | Table of every agent run in this tenant — newest first. Columns: agent key, version, status (RUNNING/WAITING_HITL/SUCCEEDED/FAILED), principal (user or agent OBO), token counts, cost, started-at. Filter by agent. | agent-runtime `GET /runs` via BFF `agentRuns` |
| **Run trace visualizer** | `/copilot/runs/{run_id}` | The **full tool-call tree** for one run: every LLM call, every tool call, every memory retrieval, every decision node. Per-node status, duration, token cost, citations. Virtualized (handles 800+ nodes). Error nodes auto-expand. Deep-linkable per span via `?span=<id>`. | agent-runtime `GET /runs/{id}` (returns full `trace` array) |
| **Approval inbox** | `/inbox` | Pending proposals across all agents. Each card: agent id + version, tool + args diff (side-by-side or unified toggle), rationale (LLM-generated), affected URNs, predicted effect, cost. Approve / Reject (reason mandatory) / Edit args. | agent-runtime `GET /proposals?status=PENDING` |
| **Proposal detail** | `/inbox/{proposal_id}` | Full proposal detail including the run's trace deep-linked, the exact tool args, and the OTel trace_id for cross-referencing to Langfuse/OTel. | agent-runtime `GET /proposals/{id}` |
| **Agent catalog** | `/admin/agents` or `/admin/tools` (depending on build state) | List of the 8 agents with cards (`<AgentCatalogCard>` component). Each card shows write mode, published versions, tenant pinning (default / canary / shadow / rollback state), kill switch status. | agent-runtime `GET /agents` |
| **Kill switches** | Admin flow inside agent detail | Per-(agent, version, tenant) kill switch. Toggle on → all runs of that version refuse to start on the next kill-registry pub/sub tick (~5s). | agent-runtime `POST /kill-switches` |
| **Rollouts** | Admin flow inside agent detail | Canary rollout (route N% of traffic to version B), shadow (run both, use A's output, log B's), rollback. | agent-runtime `POST /rollouts` |
| **Audit search** | `/admin/audit` | Every agent-related action with dual attribution. Filter by `actor.type=agent` OR `via_agent.agent_id=case-triage` to see the agent's entire footprint. | audit-service `GET /events` |
| **AI cost panel** | `/admin/usage` | Token counts + cost by tenant/workspace/model. **Per-decision attribution (USG-FR-080..086) is design-only; you'll see totals but not per-agent-run cost until BRD 17 §3.8 is implemented.** | usage-service |

## 5. Where agents WRITE — event topics and databases

Agents don't just render in the UI — they emit events and update rows. Watching these is often the fastest way to see what an agent just did.

### 5.1 Kafka topics (Redpanda in dev)

```bash
# List all agent-related topics
docker exec -it $(docker ps -qf name=redpanda) rpk topic list | grep -E "ai\.|agent\.|proposal|case"

# Watch agent runs happen in real time
docker exec -it $(docker ps -qf name=redpanda) rpk topic consume ai.agent_run.v1 --num 5

# Watch tool invocations
docker exec -it $(docker ps -qf name=redpanda) rpk topic consume ai.tool_invoked.v1 --num 5

# Watch LLM usage events (token counts per call)
docker exec -it $(docker ps -qf name=redpanda) rpk topic consume ai.token_usage.v1 --num 5

# Watch proposals appear + decisions land
docker exec -it $(docker ps -qf name=redpanda) rpk topic consume agent.events.v1 --num 10

# Watch downstream writes (case dispositions after approvals)
docker exec -it $(docker ps -qf name=redpanda) rpk topic consume case.events.v1 --num 5
```

### 5.2 Databases (agent-runtime PG DB)

```bash
# Connect to agent-runtime DB
docker exec -it $(docker ps -qf name=postgres) psql -U windrose -d agent_runtime

# Last 5 sessions
\x on
SELECT id, tenant_id, agent_key, principal_id, created_at FROM sessions ORDER BY created_at DESC LIMIT 5;

# Last 5 runs with tokens + cost
SELECT id, session_id, agent_key, agent_version, status, input_tokens, output_tokens, cost_usd, started_at FROM runs ORDER BY started_at DESC LIMIT 5;

# Pending proposals
SELECT id, run_id, agent_key, tool_id, status, created_at FROM proposals WHERE status='PENDING' ORDER BY created_at DESC;

# The full trace of a run (large JSON — use \gset or a viewer)
SELECT trace FROM runs WHERE id='<run_id>';
```

### 5.3 Logs

```bash
# All agent-runtime activity
tail -f logs/agent-runtime.log

# LLM traffic
tail -f logs/ai-gateway.log

# Tool invocations + authz decisions
tail -f logs/tool-plane.log

# Case-service writes triggered by approved proposals
tail -f logs/case.log

# All at once
tail -f logs/{agent-runtime,ai-gateway,tool-plane,case,memory,realtime-hub}.log
```

Look for structured lines like:
- `agent_run.completed run_id=... agent=case-triage tokens_in=... tokens_out=... cost_usd=...`
- `proposal.created proposal_id=... run_id=... tool=case.apply_disposition`
- `proposal.approved proposal_id=... approver=user-manager grant_issued=true`
- `tool.invoked tool=case.apply_disposition grant_verified=true`

## 6. How to trigger an agent manually (for testing)

Sometimes you want to poke an agent without going through the UI. Three ways, easiest first.

### 6.1 Via UI — chat message with a context URN

Log in as any persona → open the copilot drawer → send a message. If you're on a case page, the drawer's context is the case URN and the meta-router will delegate to `case-triage` if the intent is triage-shaped.

### 6.2 Via UI — case with copilot suggestion

Go to `/cases/CLM-1001`, hit the "Ask copilot" or "Generate disposition" button (varies by build). This posts to `agent-runtime POST /sessions` with `context_urn=wr:<tenant>:case:case/CLM-1001` and `agent_key=case-triage`.

### 6.3 Via HTTP — direct API call

```bash
# Get a token for the adjuster persona (dev mode)
TOKEN=$(curl -s -c /tmp/w.jar -X POST http://localhost:3000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"adjuster@demo.windrose"}' && \
  grep -o 'windrose_session=[^;]*' /tmp/w.jar | cut -d= -f2)

# Create a run for the analytics agent (read-only, safe to call any time)
curl -s -X POST http://localhost:8306/api/v1/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_key": "analytics",
    "context_urn": "wr:t-demo:workspace:workspace/ws-claims",
    "input": {"question": "How many claims are OPEN by severity?"}
  }' | python3 -m json.tool

# Poll the run to completion
RUN_ID="<from response above>"
curl -s http://localhost:8306/api/v1/runs/$RUN_ID -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Then open `/copilot/runs/$RUN_ID` in the browser to see the full trace visualizer for that run.

## 7. The seeded triage flow, traced step by step

`seed_claims_demo.py` runs the triage copilot on two cases so PENDING proposals sit in the inbox. Here's exactly what happened, and what you can inspect afterward:

```
seed_claims_demo.py:create_open_cases()
   ↓ POST case-service /cases (creates OPEN case rows)
   ↓ POST agent-runtime /runs {agent_key: "case-triage", context_urn: <case URN>}
   ↓
agent-runtime creates session + run rows
   ↓ Temporal AgentRunWorkflow starts
   ↓
triage.v1 graph executes:
   1. NODE load_case
      → tool-plane POST /tools/invoke {tool: "case.get", args: {case_urn}}
      → tool-plane calls case-service GET /cases/<id>
      → adds case row + display projection to state
   2. NODE fetch_similar
      → memory-service similarity search over resolved-case RAG
      → returns top-K similar resolved cases with their dispositions
      → adds RAG citations to state
   3. NODE llm_reason
      → ai-gateway POST /v1/chat/completions (real Ollama, qwen2.5:0.5b or llama3.2)
      → prompt: case data + similar-cases RAG + policy hints
      → response: severity + assignee + disposition + rationale + citations
      → emits ai.token_usage.v1 event with real token counts
   4. NODE propose
      → creates proposal row (status=PENDING)
      → does NOT execute — this is the proposal-mode boundary
      → emits proposal.created event on agent.events.v1
      → run.status → WAITING_HITL
```

**What to look at after seed finishes:**

1. Browser: `/inbox` — you see the pending proposals.
2. Click a proposal → click the **"View trace"** link → land on `/copilot/runs/<run_id>` — see the full 4-node tree.
3. Terminal: `docker exec -it $(docker ps -qf name=redpanda) rpk topic consume agent.events.v1 --num 20` — you see the actual events in chronological order.
4. Log: `grep "case-triage" logs/agent-runtime.log | head -20` — you see the real log lines with run IDs, latencies, node transitions.

## 8. Adding a new agent (the pack thesis proof)

The catalog is defined declaratively — adding a new agent is a **definition + graph module** with **no runtime fork**:

```python
# services/agent-runtime/app/agents/catalog.py
CATALOG["fraud-review"] = (
    "Fraud Review Copilot",
    "Proposes FWA scoring on submitted claims grounded in policy + historical patterns.",
    "proposal",                          # write mode
    "fraud_review.v1",                   # graph ref
    [{"id": "score_claim_fwa",           # skill
      "description": "Propose a FWA score for a claim",
      "tags": ["fwa", "claims", "proposals"]}]
)
```

Then create `services/agent-runtime/app/graphs/fraud_review.py` implementing the LangGraph state machine. Register the tool in tool-plane. Ship a golden eval set in eval-service. Restart agent-runtime — the new agent's card is signed and the seeded workflow becomes available.

This is exactly how the `insurance-claims-payer` capability pack (BRD 24) will materialize its Prior-Auth, Appeal Analyst, and Denial-Rationale agents — a definition + graph per agent, no core changes.

## 9. Debugging — the 8 most common questions

**Q: I asked the copilot something but got a generic response. What happened?**
Check `logs/agent-runtime.log` for the session id → find the run → see if `meta-router` routed to a specialist or fell back to a generic path. If Ollama is slow or unreachable, the response can degrade — check `logs/ai-gateway.log` for `ollama` calls with timing.

**Q: I approved a proposal but nothing changed.**
1. Check `logs/agent-runtime.log` for `grant_issued=true`.
2. Check `logs/tool-plane.log` for `grant_verified=true` and `tool.invoked`.
3. Check `logs/case.log` for the write.
If the grant was issued but tool-plane refused: the agent's toolset was wrong. If tool-plane accepted but case-service refused: authz issue. If all pass and no visible change: SSE not delivering to your browser — hard-refresh.

**Q: The trace visualizer is empty for a run.**
The run may still be RUNNING (trace serializes at each node completion) or the trace persistence failed. Check `logs/agent-runtime.log` for the run id — every completed node prints its span.

**Q: I don't see my run in `/copilot/runs`.**
That endpoint returns only your tenant's runs. If you're on adjuster and the run was scheduled by the governance agent under a different principal, filter — check the audit view (`/admin/audit`) which shows all runs.

**Q: How do I know which model was used?**
The run's `trace` array has an `llm_call` node with `gen_ai.request.model` attribute. Also: every `ai.token_usage.v1` event names the model + provider + rung. Also: `/admin/ai-gateway/ladders` shows the current per-class ladder.

**Q: How do I kill a runaway agent?**
`/admin` → agent detail → kill switch ON. Propagates via Redis pub/sub in ~5s. Existing in-flight runs continue to `SUCCEEDED` or `FAILED`; new runs refuse to start.

**Q: The inbox badge doesn't decrement after approval.**
SSE disconnected — check the network tab for an EventSource on `/api/rt/*`. Reconnect: reload the page. Longer-term fix: check `logs/realtime-hub.log` for connection errors.

**Q: How do I see cost per agent run?**
On the run detail page (`/copilot/runs/{id}`), the Stat panel shows Cost / Input tokens / Output tokens. In the run list, "Tokens" column shows in→out counts. **Per-decision aggregation across runs (USG-FR-080..086) is design-only — not yet computed.**

## 10. The one-paragraph summary

Windrose agents are LangGraph state machines wrapped in Temporal workflows, executed inside `agent-runtime`. Every LLM call goes through `ai-gateway` (budgets + guardrails). Every tool call goes through `tool-plane` (MCP registry + signed-grant authz). Every intent to write becomes a `Proposal` row that a human decides on in `/inbox`; approval issues a JWKS-signed grant that `tool-plane` verifies before the domain service (case-service, etc.) accepts the write. Full visibility surfaces are: `/copilot/runs` (list), `/copilot/runs/{id}` (trace visualizer), `/inbox` (proposals), `/admin/audit` (every action). Backing evidence is in Kafka topics `ai.*`/`agent.events.v1`, the `agent_runtime` Postgres DB, and `logs/agent-runtime.log`.

## 11. Cross-references

- **BRD 14 agent-runtime** — the specification (agent registry, sessions, runs, proposal framework, HITL, signing).
- **BRD 13 tool-plane** — MCP registry + tool-scope authz + grant verification.
- **BRD 12 ai-gateway** — LLM routing + budgets + guardrails; §3.8 cost mechanisms (design-only).
- **BRD 24 insurance-claims-payer** — the three payer agents (Prior-Auth, Appeal Analyst, Denial-Rationale) that will land on this same infrastructure.
- **WALKTHROUGH.md** — persona-driven testing (the story arc the agents participate in).
- **WINDROSE_MODEL_STRATEGY.md** — how the ai-gateway decides between SLM (own GPUs) vs hosted frontier LLMs.

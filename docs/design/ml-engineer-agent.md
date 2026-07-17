# Design — ml-engineer agent (BRD 52)

Autonomous train → evaluate → propose-promotion, human four-eyes on promotion.
Grounded in the on-disk contracts (agent-runtime graph registry, tool-plane
enforcement, pipeline/experiment/dataset APIs). Companion to
`docs/brd/52_ml_engineer_agent_BRD.md`.

## 1. Decomposition onto existing rails

```
user (DS) opens agent session: {dataset, target_column, [candidate_cap]}
   │
   ▼  ml_engineer.v1 graph (agent-runtime, OBO = requesting user)
ground     dataset_reader: schema/version/rows  +  pipeline_reader: algorithm catalog
plan       LLM picks ≤N candidate algorithms + fills TrainingSpec per candidate
           (label_column validated against real schema; supervised-capable algos only)
train ×N   pipeline_writer: POST /algorithm-templates/{name}/pipelines (OBO token)
poll       pipeline_reader: GET /runs/{id} until finished/failed (bounded)
collect    metrics + model refs from run_payload (mlflow_run_id, model_uri, metrics)
compare    deterministic ranking on primary metric; LLM writes the comparison rationale
propose    WriteIntent(tool_id=experiment.model.promote, tier=write-proposal,
           required_action=experiment.model.update, evidence-carrying rationale)
   │
   ▼  existing proposal machinery (ai.proposal, inbox, eligibility, no self-approval)
human approves → signed grant → tool-plane → experiment-service /internal MCP facade
   → Promotion(pending, requested_by=approver-of-proposal)
   → SECOND human decides promotion (experiment-service four-eyes: self-approval 403)
```

Two-layer HITL is deliberate: proposal approval authorizes *requesting* the
promotion; experiment-service's own four-eyes still governs the promotion
itself (`SelfApprovalForbidden`, services.py). The agent can never touch
either gate.

## 2. Autonomy mapping (BRD MLE-FR-020 → mechanisms)

| Step | Mechanism | Why it's safe |
|---|---|---|
| inspect/catalog/poll | GraphDeps readers (existing adapter pattern) | read-only |
| launch training | NEW `pipeline_writer` adapter calling the same REST route the UI uses, authorized by the OBO user's own `pipeline.template.create` | reversible sandbox artifact; identical authz+audit as a human click; ai-gateway budgets cap LLM spend; agent kill-switch halts sessions |
| propose promotion | `WriteIntent` → proposal (auto-execute impossible: policy layers never auto-execute; approver executes via tool-plane grant) | the consequential act stays human |

v1 trade-off (recorded honestly): training launches are OBO-direct service
calls, not tool-plane invocations — the engine supports exactly one
WriteIntent per run, so N training launches cannot ride the proposal channel
in a single session. Phase 2 (Temporal mode) moves them onto auto-executed
write-proposal tools for uniform tool-plane telemetry. Authz/audit are NOT
weaker in v1: rbac checks the human's own action on every launch and
pipeline-orchestrator audits each create.

## 3. Changes by service

### 3.1 experiment-service (closes gap: promote facade unreachable)
- NEW route `POST /internal/v1/mcp/invoke` (mirror pipeline-orchestrator
  `routes/internal.py`): validates the tool-plane invocation envelope,
  re-checks OPA for the obo human (`experiment.model.update` for
  `experiment.model.promote`), dispatches to the existing MCP facade
  (`model_promote` → promotion request). 4xx bodies surface verbatim (the
  in-flight tool-plane `backend_rejected` change now propagates them).

### 3.2 tool-plane registrations (data, not code)
- Register + publish + tenant-enable `experiment.model.promote`
  (write_proposal, reversible — a promotion *request* is reversible; the
  promotion itself has its own gate) and `pipeline.template.create_from_algorithm`
  (write_proposal, reversible — closes the pre-existing model-training gap).
- `mcp_backends` rows: `experiment-service` → `{EXPERIMENT}/internal/v1/mcp/invoke`;
  `pipeline-orchestrator` → `{PIPELINE}/internal/v1/mcp/invoke`.
- Recipe: new `register_ml_lifecycle_tools()` in `deploy/e2e/lib/seed.py`
  mirroring `register_inference_tool` (semantic_description ≥40 chars with
  "use when", x-windrose-urn annotations, deprecate-then-publish idempotency).

### 3.3 agent-runtime
- `app/adapters/pipeline.py`: add `PipelineWriter.instantiate(algorithm, body,
  tenant, obo_token)` → `POST /api/v1/algorithm-templates/{name}/pipelines`;
  non-201 raises a typed error the graph converts to an honest failure report.
- `app/graphs/ml_engineer.py`: `@register("ml_engineer.v1")`; StateGraph
  `ground → plan → train_all → collect → propose`; deterministic caps
  (candidates ≤3, poll ≤120s×interval, per-candidate timeout); LLM outputs
  schema-validated before any launch; `run_ml_engineer(deps, inputs)`.
- `app/graphs/__init__.py`: import + `RUNNERS["ml-engineer"]`.
- `app/agents/catalog.py`: CATALOG entry (write_mode="proposal",
  graph_ref="ml_engineer.v1", skills) + toolset branch
  `experiment.model.promote >=1.0.0`; `seed_catalog()` publishes v1 at boot.
- `GraphDeps.pipeline_writer` + `RunEngine._deps` + `container.py` wiring
  (real + fake for tests). Minimal additive edits — several of these files
  carry in-flight uncommitted changes from parallel work; do not reorder or
  reformat surrounding code.

### 3.4 No UI work
Proposals surface in the existing inbox; sessions in the existing agent
views; promotion decisions in the existing ML promotion surface.

## 4. Evidence contract (MLE-FR-030)
Proposal rationale is assembled deterministically (not LLM-freeform):
dataset name/version/rows, target column, per-candidate {algorithm, run_id,
status, primary metrics}, winner + margin, registered model name/version,
and the LLM's comparison narrative appended. Metrics are copied verbatim
from run payloads — the LLM never writes a number.

## 5. Failure posture
Any step failure → GraphOutcome with final_text = honest report (what ran,
what failed, artifacts created so far) and NO write intent. Unsuitable
dataset (missing target, zero rows, no runnable supervised algorithm) is
detected in `ground/plan` before any launch.

## 6. Test plan
- Unit (agent-runtime pytest, fake deps): plan validates target against
  schema; caps enforced; propose carries full evidence; failure paths emit
  no intent.
- Contract: experiment-service internal invoke route (200 happy, 403 obo
  without action, 422 bad envelope).
- Live e2e (task): in wr-disputes as admin → session on cd-disputes/
  disposition-ish numeric target → ≥2 real pipeline runs → proposal in inbox
  → approver approves → pending Promotion exists → second user decides →
  audit chain + transcript verified. Negative: agent session user without
  experiment.model.update → caller-gate rejects proposal creation.

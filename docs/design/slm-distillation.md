# Design — SLM Distillation Loop (self-learning, cost-reducing small models)

**Status: DESIGN (not built).** This is the platform's core differentiating thesis —
*governed self-learning that builds tenant-specific small language models (SLMs)
over time and drives AI cost down*. It is a genuine multi-milestone feature; this
document is the design-before-code artifact (per the engineering rules). Every
stage is anchored to the real service that already owns the relevant substrate,
so this is an integration/extension plan, not greenfield-from-zero.

## Thesis & cost mechanism

Frontier models are used to *bootstrap* quality; over time, a distilled per-tenant
(or per-archetype) SLM absorbs the recurring, narrow decision patterns and serves
them at a fraction of the cost/latency. Cost falls because ai-gateway's **ladder**
already routes a request to the cheapest capable "rung"
(`ai-gateway/app/domain/pipeline.py` `ladders.select_rung`, `min_rung`, escalation):
once a tenant SLM is registered as a low rung and passes eval gates, the router
sends the matching traffic to it and only escalates to a frontier rung on low
confidence. No new routing concept is needed — the SLM becomes a new bottom rung.

## The learning loop (governed)

```
agent runs ─▶ transcript capture ─▶ curation+consent ─▶ SFT dataset ─▶ train (GPU)
   ▲                                                                        │
   │                                                                        ▼
ai-gateway rung  ◀── eval-gated promotion ◀── registered candidate ◀── distilled SLM
```

Human corrections are the highest-value signal — the platform already routes agent
writes through proposals (four-eyes) and case dispositions; an *approved correction*
of an agent proposal is a gold (input → corrected-output) training pair. That
human-correction→retrain loop is the hero journey and the differentiator.

## Stage-by-stage (owner service → what exists → what to add)

### 1. Transcript capture — `agent-runtime`
- **Exists:** every run executes through `runtime/engine.py` + Temporal activities
  (`runtime/temporalx/activities.py`); grounding/design nodes already record a
  trace; `memory-service` persists episodic run state.
- **Add:** a durable, PII-tagged **transcript sink** — (system prompt, grounding
  context, tool calls, model output, model+rung used, tokens/cost, and the
  eventual *human decision*: proposal approved/edited/rejected, case disposition).
  Emit to an append-only store (reuse the outbox→Kafka→object-store pattern used by
  audit-service; land Parquet in the warehouse via dataset-service ingestion).

### 2. Curation + consent — new `distillation-service` (or a module in eval-service)
- **Reuse:** eval-service already owns governed NL→SQL/verified-query curation +
  four-eyes review + embeddings; the same review spine applies to training pairs.
- **Add:** curation that (a) filters to consented tenants (tenant-scoped opt-in;
  RLS-isolated — never cross-tenant train without explicit contract), (b) dedups +
  redacts PII (reuse ingestion-service's PII tagging), (c) prefers
  human-corrected pairs, (d) quality-scores via an LLM-judge (eval-service
  `judge_client` already exists), (e) emits a versioned **SFT dataset** as a
  first-class dataset-service dataset (so it's browsable, lineage-tracked,
  governed like any other).

### 3. SFT template + archetypes
- **Archetypes:** cluster transcripts by (vertical pack, agent persona, task
  shape) — the 7 packs (payer/caremgmt/rcm/fwa/pbm/pac/aml) are natural archetype
  seeds. An SLM is trained per archetype (shared) or per high-volume tenant.
- **SFT template:** a chat-format JSONL builder (system+grounding → assistant
  output), with the tenant's semantic model + verified queries as in-context
  grounding so the SLM learns the *governed* answer, not raw generation.

### 4. Training — GPU nodepool + `experiment-service`
- **Exists:** deploy layer has AKS/GKE (`deploy/terraform/{azure/aks.tf,gcp/gke.tf}`)
  — add a GPU nodepool (taint/toleration + accelerator label) and a Helm-templated
  training Job/`pipeline-orchestrator` workflow (it already runs Argo/Temporal
  executors). MLflow tracking exists in experiment-service — training runs, params,
  and the resulting SLM artifact register there as a normal model version.
- **Add:** LoRA/QLoRA fine-tune job (cost-efficient), teacher = the frontier rung
  that produced the accepted outputs; student = a small open base (served via
  inference-service, which already exists for model serving).

### 5. Eval-gated promotion — `eval-service` + `ai-gateway`
- **Exists:** eval-service already has suites, cases, gate rules, and a promotion
  approval workflow; ai-gateway already has rungs + per-model pricing.
- **Add:** a candidate SLM must pass its archetype's eval suite (accuracy vs the
  frontier baseline within tolerance, cost/latency budget) before an admin
  promotes it. Promotion = registering the SLM as a new low rung in the tenant's
  ladder with a confidence-based escalation threshold. Roll back = demote the rung.

### 6. Continuous improvement
- Each retrain consumes the newest human corrections; drift monitors (usage-service
  already tracks per-tenant spend/latency) trigger retrain when the escalation rate
  climbs (SLM losing coverage) or cost savings decay.

## Governance & safety (non-negotiable, mostly already present)
- Tenant data **never** leaves its RLS boundary un-consented; per-tenant/per-archetype
  isolation.
- Every stage audited (audit-service hash chain).
- No SLM serves traffic without passing eval gates + explicit admin promotion
  (four-eyes), and every SLM answer still flows through the same
  proposal/HITL/authz path as a frontier answer — the SLM is cheaper, not less
  governed.

## Dependency & chip note (answering the original product question)
- **End-to-end capability:** yes — capture (agent-runtime), curate (eval-service),
  store (dataset-service/warehouse), train (GPU nodepool + experiment-service/MLflow),
  serve (inference-service), route-for-cost (ai-gateway ladder), gate
  (eval-service). The spine exists; the distillation-service + GPU job + rung
  registration are the net-new pieces.
- **Dependency storage:** the SFT datasets + model artifacts are versioned in
  dataset-service/MLflow with lineage, so a model is always traceable to the exact
  transcripts + corrections it learned from (auditability + right-to-erasure).
- **Cost-effective chip solution:** LoRA/QLoRA + small (7–8B-class) students make a
  single commodity GPU (or spot GPU nodepool, auto-scaled to zero between jobs)
  sufficient; serving the distilled SLM is CPU/GPU-cheap vs. frontier API calls —
  that delta IS the cost reduction. No bespoke silicon required; the win is
  routing narrow, high-volume, governed decisions off the frontier rung.

## Milestones (buildable increments, each shippable)
1. Transcript sink in agent-runtime (+ human-decision join). *(small-medium)* —
   **BUILT + VERIFIED (2026-07-15).** `agent_transcripts` table (migration 0006,
   RLS forced + isolation policy + grants, applied live), `domain/redact.py`
   (PII redaction), `domain/transcripts.py` `TranscriptSink` (consent-gated,
   best-effort capture at run completion via `RunEngine.execute`; human decision
   joined in by `ProposalService.decide` — approve/edit/reject → the gold
   input→corrected-output pair), store methods (SqlStore + InMemoryStore),
   `GET /api/v1/transcripts` read API, `slm_transcript_capture` consent flag.
   102 agent-runtime unit tests (7 new), migration applied to the live DB,
   service restarted healthy with the route live.
2. Curation → versioned SFT dataset (consented, PII-safe, human-correction-weighted). *(medium)* —
   **BUILT + VERIFIED (2026-07-15).** `sft_datasets` + `sft_examples` tables
   (migration 0007, RLS forced + policies + grants, applied live), `domain/
   sft_template.py` (transcript → OpenAI chat example; edit→corrected_output is
   the gold target, approve→accepted args), `domain/sft_curation.py` `SftCurator`
   (reads consented+decided transcripts → templates → drops degenerate → dedups
   by (input,target) hash → content-addressable checksum → immutable versioned
   snapshot; re-curation mints the next version; archetype-scoped by agent_key),
   store methods (SqlStore + InMemoryStore), `POST /api/v1/sft-datasets` (curate)
   + list/get + `GET /{id}/examples` JSONL export (the exact artifact the LoRA
   trainer consumes). 108 unit tests (6 new), + LIVE-verified against real
   Postgres with the real record→attach-decision flow (row_count 2 from an
   edit-gold-pair + an approve, reject/unconsented excluded; RLS cross-tenant
   isolation confirmed); migration applied live, route live (401 gated).
3. GPU nodepool + LoRA training job → MLflow-registered candidate. *(medium)*
4. Eval-gate + admin promotion → ai-gateway low rung + escalation. *(medium)*
5. Drift-triggered retrain + savings dashboard. *(medium)*

Each milestone is independently valuable and testable; #1–#2 already unlock a
governed, browsable corpus of human-corrected decisions even before any model is
trained.

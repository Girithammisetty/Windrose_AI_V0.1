# Windrose — Decision-Intelligence Completeness Roadmap

The gap analysis and sequencing to move Windrose from "a decision-intelligence
platform by architecture" to "one that passes the category checklist in a
bake-off." Anchored on Gartner's SIX mandatory DI capabilities and THREE modes.
Honest status per gap: BUILT / DESIGNED / INFRA-GATED.

## Scorecard (where we stand)

| Gartner capability | Status | Owning BRD |
|---|---|---|
| Decision Execution | ✅ built | Core (pipelines, agents, write-back) |
| Decision Collaboration | ✅ built (leading) | BRD 52/53 (proposals, four-eyes, guardrails) |
| Decision Service Composition | ✅ built (leading) | BRD 23 pack framework |
| Decision Governance | ✅ built (leading) | audit + RBAC + BRD 53 guardrail envelope |
| **Decision Modeling** | ⚠️ **the real gap** | **BRD 54 (new) — inc1 BUILT** |
| Decision Monitoring | ⚠️ partial (metrics yes, outcomes thin) | **BRD 55 (new) — designed** |
| *(data unification: entity resolution)* | ⚠️ gap | **BRD 56 (new) — designed** |

Three modes: Decision Support ✅, Decision Augmentation ✅ (hero flow), Decision
Automation = deliberately augmentation-first (a positioning choice for regulated
verticals, validated by the "overautomation risk" the category itself flags).

## The three genuine category gaps (new BRDs)

### BRD 54 — Decision Modeling (governed decision tables / rule sets)
The one capability a Gartner evaluation marks INCOMPLETE today: Windrose's
decision logic lives in pack dispositions + agent prompts + ML models, not in a
visual/config, business-user-editable **decision model**. BRD 54 adds explicit,
versioned, testable decision logic (condition→outcome rules over real dataset
columns) that EXECUTES to the same governed four-eyes proposal — deterministic,
explainable (which rule fired), no code, no LLM. It is the "rules" leg of the
three modes and reuses the BRD 53 guardrail + proposal machinery wholesale.
**Increment 1 BUILT + tested + live-verified** (see BRD 54 §7).

### BRD 55 — Decision Outcome Monitoring
Moves monitoring from "model metrics + captured corrections" to "did decisions
of this TYPE produce good OUTCOMES over time?" — outcome labels joined back to
decisions, decision-effectiveness KPIs, drift on the DECISION (not just the
model), and a feedback signal that strengthens the learning loop. DESIGNED;
composes with the existing eval-service + correction corpus + dashboards.

### BRD 56 — Entity Resolution
The data-unification gap (Quantexa's specialty), acute for banking/AML: build
unified views of a customer/counterparty/supplier across fragmented records
before decisions run. DESIGNED; honestly the largest lift and the one most
justifying a "buy a component vs build" conversation — sequenced last.

## Already-designed increments (existing BRDs — referenced, not re-authored)

These gaps are already captured with a sequence; they are increments of shipped
BRDs, not new category gaps:

- **BRD 52 inc 2-3** — agent-initiated ingestion, scheduled drift-driven retrain;
  and the live promotion leg unblocked once the MLflow→experiment mirror is
  wired (in-flight task).
- **BRD 53 inc 2-3** — per-agent data-scope enforcement, per-agent budgets,
  PII-egress block, the author UI, persona auto-binding for all pack roles,
  operator platform-ceiling console.
- **Learning loop M3-5** — GPU LoRA train/promote/retrain. INFRA-GATED (needs a
  GPU; MLX-on-Mac can prove the mechanism without rental).
- **Case-evidence increment 2** — agent reasoning over unstructured evidence
  (docs/photos). Greenfield Core feature.
- **BYO hardening P1-4** — OTel wiring, secrets adapters, SIEM export, IdP/OIDC.

## Sequencing (value × buildability, honest)

1. **BRD 54 Decision Modeling** — highest category-value, buildable on frozen
   Core, reuses guardrails. → inc1 DONE; inc2 = visual authoring UI + richer
   operators + decision-model-as-pack-artifact.
2. **BRD 55 Outcome Monitoring** — medium lift, strengthens the differentiator
   (the learning loop), composes with eval-service. → build next.
3. **BRD 53/52 deferred increments** — harden what's live (data-scope, budgets,
   author UIs, mirror). → interleave.
4. **BRD 56 Entity Resolution** — largest lift; evaluate build-vs-buy (a graph/
   ER component) before committing. → sequenced last.
5. **Learning loop M3-5** — when a GPU (or MLX-on-Mac proof) is available.

## What "true DI platform" means when this is done
All six capabilities present and demonstrable: decisions are MODELED (BRD 54),
EXECUTED (Core), COLLABORATED on with humans + guardrails (BRD 52/53), MONITORED
by outcome (BRD 55), COMPOSED as reusable packs (BRD 23), and GOVERNED end to end
(audit + guardrail envelope) — over data UNIFIED including entity resolution
(BRD 56). Category-complete, with governance/collaboration as the moat.

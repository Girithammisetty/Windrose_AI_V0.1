# Datacern AI — SI Partner Meeting Briefing

**Prepared:** 2026-07-21 · **Audience:** internal prep for meeting a solution-implementation firm
**Scope of the conversation:** testing help, offshore engineering resources, infrastructure support, GTM assistance.

Sections are marked **[SHARE]** (safe to present/hand over) or **[INTERNAL]** (your prep only — decide deliberately if/when to disclose).

---

## 1. The 60-second pitch — [SHARE]

**Datacern AI is a governed Decision Intelligence platform for regulated operations** — insurance claims, banking AML, disputes, appeals, and 20+ other case-based verticals. AI agents draft decisions with cited evidence; humans stay in command through a four-eyes approval gate; every human correction becomes training data that retrains and re-promotes models under the same governance. The differentiator is not "AI that decides" — it's **AI decisions your regulator can audit**, with a learning loop that makes cost-per-decision *decline* over time.

One platform Core, frozen and versioned. Verticals ship as installable **Capability Packs** (28 built today) — the same model SAP/Salesforce used to build partner ecosystems.

---

## 2. Platform current state — [SHARE]

### What is built and working (all live-verified, not slideware)

| Area | State |
|---|---|
| **Core services** | 23 microservices: 10 Go (identity, RBAC, case, tool-plane ×2, audit, notification, usage, chart, query, realtime), 11 Python (agents, AI gateway, data/ML plane), GraphQL BFF, Next.js UI |
| **Agentic layer** | 9 production agents (triage, copilot, ML-engineer, etc.) + tenant-defined custom agents, all guardrail-enforced (data-scope, token budgets, PII-egress redaction) and tool-gated through a governed tool plane |
| **Governance** | Four-eyes approval on every AI-proposed write; self-approval rejected at the API level; risk tiering; approval anti-laundering; full decision trace (input snapshot → rationale → outcome) |
| **Learning loop** | Proven end-to-end with real components: human correction → labeled dataset → real model training (MLflow) → governed promotion → batch inference on new work. No mocks anywhere in the demo path |
| **Vertical packs** | 28 packs across healthcare, banking, insurance, manufacturing, logistics, trust & safety. Pack framework + CLI (`packctl`) + registry + install/upgrade/rollback/drift-detection |
| **Data plane** | Ingestion (CSV/JSON/Parquet/Avro/XML + X12/FHIR/HL7v2/ISO20022/ACORD), Iceberg lakehouse on S3-compatible storage, Trino + DuckDB query engines, semantic models, entity resolution with governed golden-record merge |
| **Enterprise readiness** | Multi-tenant (Postgres RLS isolation), per-tenant BYO OIDC IdP, BYO secrets (Vault/AWS/Azure/GCP), SIEM audit export, 7-year WORM audit store (ClickHouse), embedded/white-label UI with per-user embed SSO |
| **Observability** | Distributed tracing + RED metrics across ~20 services, health self-diagnosis tooling (`make doctor`) |

### Engineering quality evidence

- **2,500+ automated tests, all green** (verified 2026-07-21): ~1,960 Python tests across 11 services (largest suite: ingestion at 600), 467 UI tests, 10 Go service suites + shared libraries.
- **Full E2E journey test** runs the entire claims lifecycle against the real stack — real local LLM (Ollama), real Kafka, real object storage, real MLflow — and passes every assertion.
- **Live Playwright browser suite** against the running product (no mocked backends).
- **60 BRDs** — every capability specified before build; documentation convention enforced (analysis → design → implement → test).
- **Security posture:** 5-tool scan baseline in CI (SAST, dependency, secrets), hardening pass completed (SSRF, SQLi, XXE, resource-exhaustion classes), prompt-injection defenses (XPIA, rule-of-two) on the agent layer.

### Deployment readiness

- **CI/CD built:** GitHub Actions CI, container image builds for all 23 services, CD pipelines for AWS / GCP / Azure.
- **IaC built:** Helm chart + Terraform modules for AWS, GCP, Azure (+ a budget option), GPU node-pool support for model training.
- **Runs fully on one machine** for demos/dev (`make up` → whole platform + seeded vertical demo in minutes) — this is also the partner's onboarding path.

---

## 3. Honest current-state gaps — [INTERNAL]

Know these cold; disclose selectively and frame each with its mitigation. A good partner will find them anyway in diligence — controlled candor builds trust.

| Gap | Reality | Frame it as |
|---|---|---|
| **No production cloud deployment yet** | Everything proven locally; Terraform/Helm exist but never applied against a real cloud account | "First production deployment is literally workstream #1 we'd hand a partner — IaC is written, needs an SRE pass" |
| **No customers / references yet** | Pre-revenue; design-partner motion defined but not started | "That's the GTM ask — we bring product, partner brings relationships" |
| **No compliance certifications** | SOC 2 / HITRUST identified as the #1 blocker to the first regulated customer; 6–12 month lead time, not started | "Certification evidence-collection is an explicit workstream we'd co-staff" |
| **Scale validated only at demo volume** | Known bottleneck list exists (documented scalability audit with ~10 items, e.g., ingest memory at large commits, no clustered OLAP config) | "We have a written scalability roadmap; perf/load testing is a named ask" |
| **Single-developer bus factor** | Whole platform built by one person + AI tooling; deep docs mitigate but don't eliminate | "60 BRDs + enforced doc convention exist precisely so a team can onboard" |
| **SLM distillation partially gated** | Training control plane done; GPU fine-tuning path needs cloud GPU capacity | Roadmap item, not a today-claim |

**Do not overshare in meeting #1:** exact test-failure history, internal incident notes, the bus-factor framing. Share the gap *categories* and the mitigation posture.

---

## 4. What help we need — the four workstreams — [SHARE]

Present these as concrete, scoped work packages. This shows them you're organized and lets them price realistically.

### WS-1 · Testing & QA (their testing offer)
- **Independent QA pass** over the 28 packs and core workflows (they own test plans + execution; you provide the E2E harness that already exists).
- **Performance/load testing** against the documented scalability targets (millions of cases per tenant); the bottleneck list is written — they turn it into load profiles and burn it down.
- **Security testing:** external pen-test + remediation cycle; this doubles as SOC 2 evidence.
- **Regression automation expansion:** grow the live Playwright suite; wire into CI as a release gate.
- *Good first SOW: 6–8 weeks, fixed scope, measurable exit criteria (load numbers, pen-test report, suite coverage).*

### WS-2 · Offshore engineering (their staffing offer)
Parallelizable, low-core-risk work first — protect the Core:
- **Pack authoring** — the framework + authoring guide exist (`packs/PACK_AUTHORING_GUIDE.md`); Core stays frozen; a pack is config/content, not platform code. This is the ideal offshore lane and also proves the "SIs ship their own packs" ecosystem thesis.
- **Connectors** — source-system connectors (core-admin systems, Snowflake/Databricks, SFTP/EDI endpoints).
- **Test automation & documentation** (feeds WS-1).
- **UI polish backlog** under your design review.
- Core platform changes stay with you (or a small vetted senior subset) until trust is established.

### WS-3 · Infrastructure & operations (their infra offer)
- **First production deployment** on one cloud (pick one — recommend whichever their team is strongest in) using the existing Terraform + Helm.
- **SRE setup:** monitoring/alerting build-out (OTel plumbing exists), runbooks, backup/DR, cost management.
- **Compliance engineering:** SOC 2 Type II (and HITRUST if healthcare-first) — evidence collection, control implementation, auditor liaison. **This is the critical path to revenue** and worth stating exactly that way.
- **Hosted demo environment** so prospects can be shown the product without your laptop.

### WS-4 · GTM (their market offer)
- **Design-partner sourcing** — the ideal customer profile is already written (regional health plans 2–5M members, mid-cap nationals, provider-sponsored plans; equivalents in banking for AML/disputes). Ask directly: *which named logos can you introduce us to?*
- **Pilot delivery** — the 90-day pilot playbook exists (shadow mode → proposal mode → ROI report); partner supplies delivery muscle.
- **Collateral:** deck, one-pager, demo video, ROI calculator — built from existing strategy/market docs.
- **Pricing validation** against a real procurement process.

---

## 5. How to work together — partnership structure — [SHARE the model, INTERNAL the guardrails]

### Recommended phasing — [SHARE]

| Phase | Model | Money flow |
|---|---|---|
| **1. Prove-out (0–3 mo)** | Paid services engagement: 1–2 fixed-scope SOWs from WS-1/WS-3. You are the client; they deliver. | You pay them (or heavily discounted/at-risk if they want to earn the partnership) |
| **2. Delivery partner (3–12 mo)** | Certified implementation partner: they deliver pilots at customers you jointly close. Services revenue is theirs; platform subscription is yours. | Customer pays each of you separately |
| **3. Ecosystem (12 mo+)** | Co-sell + reseller margin; they publish their own packs on the marketplace (this is an explicit strategic milestone — "an SI shipping a pack we didn't build is the moment we know we're a platform") | Referral/resale margins + their pack revenue |

Market-typical economics to anchor on (directional): referral fee ~10–15% of first-year subscription; reseller margin ~20–30%; SI services revenue on an enterprise deployment typically runs 1–3× the platform subscription — **that services pool is the reason this partnership is attractive to them; say so explicitly.**

### Non-negotiable guardrails — [INTERNAL]

1. **IP:** all work is work-for-hire with IP assignment to you. No co-ownership of Core, no white-label/OEM rights in early phases, no source-code escrow beyond standard terms.
2. **No exclusivity** in any geography/vertical until they've earned it with closed revenue — resist this hard in meeting #1; offer "first-mover preference" language instead.
3. **No equity for services** at this stage.
4. **Code access tiers:** packs/tests/docs repos for offshore teams; Core access only for named, vetted seniors; branch protection + your review as merge gate.
5. **Offshore security:** no production or customer data offshore, ever; isolated dev environments (the `make up` local stack is purpose-built for this); named-resource lists; their ISO 27001/SOC 2 posture verified; right to audit; background checks on Core-access engineers.
6. **Governance cadence:** weekly delivery standup, monthly steering, milestone-gated SOW payments with written acceptance criteria. Every SOW gets a definition of done.

---

## 6. Platform pricing model — what to say when they ask — [SHARE]

The pricing architecture (already designed into the product — metering, budgets, and cost attribution are built):

1. **Platform floor** — annual subscription per tenant/use-case. Predictable line the CFO can budget.
2. **Per-decision usage** — consumption pricing tied to the unit of value (a governed decision), with volume tiers. Cost attribution per decision is already instrumented in the product.
3. **Hard budget caps** — customer-set circuit breakers, so an AI bill can never run away. This is a *selling* feature: 73% of agentic-AI projects bust budget; ours structurally can't.
4. **Packs as add-ons** — each vertical pack is a separately priced module on the Core subscription.
5. **Bounded professional services** — platform PS limited to first-pilot integration (~weeks 0–4); beyond that, delivery is self-serve or **certified-partner territory** — i.e., the long-tail services revenue is deliberately reserved for partners like them.
6. **The kicker:** cost-per-decision *declines* with tenure (deterministic-first routing + model distillation), so gross margin improves per customer over time — the opposite of typical agentic-AI economics.

**Design-partner terms** (for the first 3 lighthouse customers): ~60% off list year 1, co-development input, case-study rights. Partner-sourced design partners still earn their referral fee on the discounted amount.

**List price:** position as "finalized per-vertical with the first design partner cohort" — don't quote hard numbers you'd have to walk back. If pressed for magnitude: mid-six-figure annual platform+pack ACV at enterprise scale is the design point, validated against the labor cost it displaces (a claims decision reviewed at $180/hr clinician time makes per-decision ROI enormous).

---

## 7. Bring-to-meeting checklist — [INTERNAL]

**Before the meeting**
- [ ] **NDA signed first** — before demo or this document's [SHARE] content in written form.
- [ ] **Demo ready:** `make up` on your machine (full platform + claims demo seeds in minutes). Rehearse the 5-minute script below. Have MLflow (`:5500`), the audit trail, and the approval inbox as "wow" tabs.
- [ ] **One-pager PDF** distilled from §1–2 of this doc.
- [ ] **Architecture diagram** (export from `docs/platform/PLATFORM_ARCHITECTURE.md`).
- [ ] **This briefing** reviewed; decide your disclosure line for each [INTERNAL] item.
- [ ] **Rate expectations researched:** know blended offshore rates for their geography so their quote has a reference point.
- [ ] **Your ask list written:** which 1–2 SOWs you'd actually sign first (recommend: WS-3 first prod deployment + WS-1 load/pen-test — both produce artifacts you need regardless of the partnership's fate).

**The 5-minute demo script**
1. Login (branded Datacern UI, role-gated) → **Cases worklist** — real seeded claims queue with severity/due ranking.
2. Open the duplicate-invoice claim → **Copilot triages it live** (real local LLM, cited evidence, confidence).
3. Show the **proposal in the approval inbox** → approve it (mention: self-approval is structurally rejected — four-eyes).
4. **Learning loop panel:** corrections → labeled examples → the retrained model in MLflow → promoted to production under approval → new claims scored by it.
5. **Audit trail:** every step above as immutable events. Close with: *"every AI action you just saw is governed, attributed, and replayable — that's the product."*

**Metrics to have memorized:** 23 services · 28 packs · 60 BRDs · 2,500+ tests green · full E2E loop with zero mocks · 3 clouds' IaC written.

---

## 8. Questions to ask THEM — [INTERNAL]

Qualify them as hard as they qualify you:

1. **Vertical proof:** Which health plans / banks have you delivered into? Named references we can call?
2. **GTM substance:** For the logos you'd introduce — who is the sponsor, what's the relationship, when did you last transact? (Separates real GTM from a slide.)
3. **Testing credentials:** Show a sample performance-test report and pen-test deliverable from a comparable engagement.
4. **Offshore specifics:** Locations, security certifications (ISO 27001? SOC 2?), attrition rate, how they handle IP assignment and data isolation for offshore teams.
5. **Compliance experience:** Have they taken a product through SOC 2 Type II / HITRUST as the implementation partner? Timeline and cost from a real example?
6. **Commercial model:** Sample MSA/SOW, rate card, and what partner tier structure they've operated in with other ISVs.
7. **Skin in the game:** Would they co-invest (discounted first SOW, at-risk pricing tied to the first closed customer) to earn preferred-partner status?
8. **Capacity & continuity:** Named team for phase 1? Key-person continuity commitments?

---

## 9. Red flags — walk-away signals — [INTERNAL]

- Demands **exclusivity** (any geography/vertical) before delivering anything.
- Wants **IP co-ownership**, broad license to the platform, or "we'll also build our own accelerator on top" ambiguity.
- **Equity for services** pushed early.
- GTM claims with **no named sponsors** ("we know everyone in payer space") and reluctance to arrange reference calls.
- Pushes to start with a **large offshore team on Core** rather than scoped packs/testing/infra work.
- Vague fixed-price aversion — insists on open-ended T&M with no milestone gates.
- No willingness to sign your IP/security terms for offshore access.

---

## 10. Suggested meeting agenda — [SHARE]

1. Intros + NDA confirmation (5 min)
2. Datacern demo — the claims journey (10 min)
3. Platform state + roadmap (10 min — §2 of this doc)
4. Their capabilities walkthrough — testing, offshore, infra, GTM (15 min)
5. The four workstreams + which two could start first (15 min)
6. Partnership phasing + commercial principles (10 min)
7. Agreed next steps: mutual reference checks, draft SOW for phase 1, second meeting date (5 min)

**Your close:** "We're looking for one partner to grow with — first two SOWs are the audition. If delivery is strong, the certified-partner services pool on every future customer is yours to lose."

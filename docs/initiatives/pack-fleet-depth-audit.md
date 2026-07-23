# Pack fleet depth audit — every vertical pack really solves its vertical

**Status:** done — 2026-07-23
**Commits:** _(filled at commit time)_  ·  **Related:** BRD 23 (pack-service), BRD 24–31 + 32–51 (pack waves), `packs/PACK_AUTHORING_GUIDE.md`, `packs/DEEP_PACK_AUTHORING_ADDENDUM.md`

---

## 1. Analysis

### 1a. Platform / product

The catalog ships 27 vertical capability packs + 1 horizontal library pack
(`investigation-framework`). All were structurally uniform (v2.0.0, data-free
binding contracts, 19 component kinds, packctl-lint clean) — but structural
uniformity says nothing about whether each pack **actually solves its vertical's
problem**: whether the decision tables encode the triage logic a real supervisor
would write, the grounding memories state regulatory facts that are TRUE and
current in 2026, the disposition catalogs cover the vertical's real outcome
space, and the KPI layer measures what an operator actually asks. A pack that
lints clean but teaches a rescinded rule, auto-proposes denials, or ships KPI
filters that can never match its own disposition codes is a liability, not a
product.

### 1b. Technical

Two-layer audit of all 28 packs:

1. **Mechanical coherence** (scripted): cross-file link checks packctl lint does
   not perform — decision-table columns ⊆ binding-contract columns ∪ case
   fields; decision/`disposition_hints` codes ⊆ disposition catalog; guardrail
   agent_keys ⊆ agent configs; `{{dataset()}}` macros ⊆ dataset names; semantic
   entity refs / dashboard measures+dimensions ⊆ semantic model; ontology
   relationship targets ⊆ entity keys; **C11**: disposition literals in semantic
   measure filters and query SQL ⊆ the pack's own disposition catalog codes.
2. **Domain truth + practitioner depth** (7 parallel audit agents, one thematic
   cohort each): regulatory currency as of 2026, rule completeness vs what a
   supervisor in that vertical would write, SME-grade agent grounding, loop
   closure (outcome space, case fields, KPIs), no-dummy-data compliance.

**Audit verdicts:** 11 deep · 15 adequate · 1 shallow (insurance-claims-payer)
· 1 base-ok (investigation-framework).

**Fleet-wide defect classes found:**

- **F1 Overdue blind spot.** Every deadline-driven decision table bucketed
  `days_to_deadline` with `between [0, n]` — an item whose window had already
  lapsed (`< 0`) fell to a low-severity catch-all or matched nothing: the exact
  inversion of real triage (a blown clock is the most urgent state).
- **F2 Disposition-vocabulary drift.** Semantic measure filters / verified-query
  SQL compared the `disposition` column against literals that were not the
  pack's own catalog codes — KPIs that would read zero forever
  (construction-claims approval/rejection rates, manufacturing-mrb 4 of 6
  disposition measures, card-disputes win rates, banking-aml SAR measures).
- **F3 Undeclared value domains.** Rules and filters hardcoded column literals
  (`'0_7_days'`, `'expedited_report_filed'`) that binding contracts never
  declared — a tenant binding a dataset with a different vocabulary gets
  silently-dead rules / zero KPIs, because pack-service validates columns, not
  value domains.
- **F4 Stale-2026 citations** (the SR 11-7 → SR 26-2 gotcha class, found 8×):
  Visa VDMP/VFMP → VAMP; 21 CFR 820.198/.100/.90 → QMSR (ISO 13485
  incorporation, effective 2026-02-02); FQHC/RHC G0511 → CY2025 unbundling;
  RPM sub-16-day categorical hold → 2026 lower-threshold code pathways; OASIS
  update calendar (Jan 1, not Oct 1); hospice F2F (3rd benefit period+, not
  every recert); CDC 90-MME reattributed to the CMS Part D OMS edit; a federal
  "rebate-delinking rule" asserted that does not exist.
- **F5 No-dummy-data residue.** insurance-claims-payer shipped fabricated
  "plan history" grounding memories citing deleted seed IDs (the one hard
  violation); device-complaints baked a tenant-specific "v4.0 rollout"
  narrative into dashboard prose; ~a dozen stale "seed" comments fleet-wide.

Plus per-pack outcome-space and rule gaps — most notably: card-disputes'
Reg E triage `default_outcome` auto-proposed **denial** for any unmatched
dispute (a UDAAP/exam-risk-shaped logic error, softened but not cured by
four-eyes); ap-invoice-audit left `suspicious_banking_change` (BEC) unrouted;
manufacturing-mrb omitted `scrap` and `repair` — two of the five canonical MRB
outcomes; workers-comp had no code to record the delay/investigation notice its
own compliance loop is named for.

---

## 2. Architecture & Design

- **Bar** = `card-disputes` v2 + `DEEP_PACK_AUTHORING_ADDENDUM.md` content bar:
  real regulatory logic with citations in decision notes, SME-grade always-on
  agent instructions, complete outcome space, KPIs an operator asks for,
  zero dummy data, routing-only tables (humans decide; tables propose).
- **Mechanical before judgment:** scripted coherence gates (incl. new C11) run
  before and after the human-judgment fixes, so the vocabulary/link classes
  become regression-checkable instead of re-audited.
- **Fix discipline (G-rules applied fleet-wide):** G1 add `lt 0 → critical`
  overdue rules wherever the deadline column exists; G2 align every disposition
  literal to catalog codes (adding honest codes where the measure was right and
  the catalog was missing the outcome); G3 declare `# value domain:` comments on
  every contract column that rules/filters compare to literals; G4 purge seed
  wording / tenant narratives; G5 version-bump every changed pack 2.0.0→2.1.0
  (library 1.0.0→1.1.0) so pack-service upgrade/rollback keys correctly;
  G6 never invent facts — anything not certainly current in 2026 is phrased
  qualitatively ("program-configured", "confirm with the acquirer/CMS");
  G7 packctl lint 0 errors / 0 warnings per changed pack.
- **Out of scope:** eval_sets stay tenant-curated (honest consequence of
  no-dummy-data — golden labels cannot be invented); `agent_recipes` stays the
  only deferred kind; auto-liability/MCS-90 lane for trucking-claims documented
  as out of scope rather than half-built.

---

## 3. Implementation & Test

All 28 packs updated to 2.1.0 (library 1.1.0) by 7 parallel fix agents, one per
audit cohort, each fix traceable to an audit finding. Highlights by class:

- **F1:** overdue `lt 0 → critical` rules added across every deadline table in
  the fleet (mortgage Reg X evaluation clock, tax response windows, AP payment
  runs, PA/appeal clocks, MDR clocks, pre-adverse windows, DSA SLA, …).
- **F2:** all semantic/query disposition literals aligned to catalog codes;
  where the measure was right but the outcome missing, honest codes were added
  (card-disputes `chargeback_won/lost`, mrb `scrap_disposition`,
  `repair_with_approval`).
- **F3:** `# value domain:` declarations added to every literally-compared
  contract column across all packs.
- **F4:** every stale citation corrected (VAMP, QMSR, G0511, RPM-2026, OASIS,
  hospice F2F, MME/OMS, delinking, CO-11, Part 370 scope, tipping-off vs
  5318(g)(2)); numbers that could not be verified current were rephrased
  qualitatively rather than asserted.
- **F5:** fabricated payer memories deleted and replaced with true generic
  CARC-semantics records; "v4.0 rollout" narrative genericized; seed wording
  purged.
- **Depth:** ~60 new decision rules (clearance blocking, catastrophic-injury,
  RTW-stall, CDP docket, trust-fund elevation, alarm-failure expedite,
  thermal-hotspot dispatch, test-buy expedite, CSAM specialist routing, per-code
  CCM time minimums, LUPA runway, SDN-vs-Entity-List split, …), ~35 new
  disposition codes, ~40 new case fields (incl. a TCM case schema), 3 new
  grounding pillars per weak pack (AKS/Stark/60-day rule, CMS-0057-F, DIR/340B,
  UFLPA, FCRA 604(b)(2), successor-in-interest, MRB quorum, rework-vs-repair),
  payer-fwa-siu's missing third dashboard (Recovery Operations), and SME
  rewrites of the thinnest agent prompts (insurance-claims-payer,
  healthcare-provider-rcm, post-acute-care).

**Verification (all post-fix, 2026-07-23):**
- packctl lint fleet-wide: 28/28 packs, 0 errors 0 warnings
- Coherence checker (C1–C11 incl. disposition-literal↔catalog gate): 0 hard
  issues across 28 packs; the C11 gate honors pharmacovigilance's documented
  SoR-vocabulary fork via its datasets.yaml value-domain declaration
- CI `test-packs` job (pytest packs/packctl/tests): 23/23 locally, post-fix
- **Live install e2e** (fresh tenant `depth-verify`, real stack): first install
  correctly **failed closed** with `requires_binding` naming each missing
  contract column; after uploading 3 real datasets through ingestion-service
  (Iceberg-committed rows), the idempotent re-install bound by same-name reuse
  and completed **card-disputes@2.1.0: 66 actions, 0 failed, 1 deferred**
  (agent_recipes) — new dispositions, updated decision tables, corrected
  memories, new verified queries all materialized; 5 per-role users verified
  live with non-empty caps under narrow tokens.
- Fleet totals after fixes (measured by the coherence checker): decision rules
  201 → 283 (+82), verified queries 135 → 158 (+23), disposition codes 165 →
  205 (+40), case fields 201 → 244 (+43), grounding memories 272 → 293 (+21),
  dashboards 80 → 81 (payer-fwa-siu Recovery Operations), charts 385 → 396.

**Deferred / honest limits:** per-claimant abuse analytics in seller-vetting
(no rights-holder dataset — not invented); payer untimely-appeal and FWA
lead-aging rules (no supporting contract columns; documented in-file);
`depends_on`/`pack_class` layering enforcement in pack-service (library pack
documents the gap); Help Center per-pack overlays remain the standing follow-up.

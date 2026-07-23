# Datacern vertical-pack authoring guide (for pack authors and agents)

> **⚠ SUPERSEDED IN PART (2026-07-22, the no-dummy-data rule).** Product packs
> ship **ZERO seed data**: no CSVs, no seeded case queue, no eval golden sets,
> no dashboards-on-demo-data. Dataset entries are **binding contracts**
> (`{identity, name, required_columns}`, no `file`) resolved to the tenant's
> REAL data at install (pack-service `dataset_bindings`). Everything below
> about seed CSVs, the 26-row main table, `cases/queue.yaml`, and per-row
> patterns applies ONLY to explicit demo packs — never to product packs.
> **Read `DEEP_PACK_AUTHORING_ADDENDUM.md` for the current grammar** (all 22
> supported kinds, exact schemas, and the binding install path). The five
> deep v2.0.0 packs (card-disputes, banking-aml, insurance-claims-payer,
> healthcare-provider-rcm, chargeback-representment) are the canonical form.

You are authoring ONE capability pack (a directory under `Datacern-ai/packs/<pack-name>/`)
plus ONE BRD (`docs/brd/<NN>_<slug>_pack_BRD.md` at the  repo root). Packs are
declarative content installed through Core public APIs by packctl — ZERO Core changes.

**Canonical reference pack: `packs/card-disputes/` — read EVERY file in it first and
mirror its structure, tone, and level of detail exactly.**
**Canonical BRD examples: `docs/brd/32_card_disputes_pack_BRD.md` (shipped-pack style,
use this) and `docs/brd/28_pharmacy_benefit_mgmt_pack_BRD.md` (house style).**

## Non-negotiable engineering rules

1. NO hallucination: grounding memories contain only well-established regulatory or
   methodological facts. If unsure of a number/date/citation, state it qualitatively.
2. NO fake/mock: capabilities Core can't materialize go in `deferred` with honest reasons.
3. All fictional entities (companies, people, products) — realistic but invented names.
   No real PII. Dollar amounts/dates plausible. Months within 2026-03..2026-06.
4. Production-ready: internally consistent data, every chart provably non-empty.

## Pack directory layout (13 component files + seed CSVs)

```
packs/<name>/
  pack.yaml                  # manifest — mirror card-disputes/pack.yaml exactly in shape
  data/datasets.yaml         # 3 datasets w/ identity (snake_case) + name (kebab-case) + csv
  data/<a>.csv  <b>.csv  <c>.csv
  semantic/<model>_core.yaml # or <domain>_core — ONE semantic model
  queries/verified.yaml      # 5 verified NL<->SQL queries
  queries/saved.yaml         # 2 saved queries (one MUST be a parent/child/n edge list)
  dashboards/<x>.yaml ×3     # 5 charts each (15 total)
  cases/dispositions.yaml    # 5 dispositions
  cases/queue.yaml           # 6 open-row cases with display_projection + rich notes
  rbac/roles.yaml            # 5 roles
  agents/configs.yaml        # case-triage + analytics TenantAgentConfig prompt_params
  memories/grounding.yaml    # 8-10 factual grounding records w/ tags + confidence
  pipelines/templates.yaml   # 2 pipelines: isolation_forest (train) + xgboost (train)
```

## Manifest (pack.yaml)

- `pack_manifest: 1`, name matches `^[a-z][a-z0-9-]{2,63}$`, version `1.0.0`,
  `publisher: { id: pub-datacern, name: "Datacern Inc." }`, description, categories,
  regulatory.
- `components:` EXACTLY the kinds in card-disputes (datasets, semantic_models,
  verified_queries, saved_queries, dashboards ×3, dispositions, cases, roles,
  agent_configs, memories, pipelines). identities match `^[a-z][a-z0-9_]{0,62}$`.
- `deferred:` all 9 kinds (guardrails, agent_recipes, connection_templates,
  write_adapters, eval_sets, ontology, case_schemas, model_archetypes, display_labels),
  each with a domain-specific honest reason. NEVER put a deferred kind in components.

## Seed data rules (CSV)

- 3 datasets forming a star or a chain: a MAIN work-item table (~26 rows: 20 closed
  with real dispositions + 6 open with disposition literally `pending`), a DETAIL/event
  or transaction table (~30 rows), and a party/product/entity table (~8-14 rows).
- All referential integrity must hold: FKs resolve, every detail row's parent exists,
  open/closed status consistent with disposition, closed rows have deadline_bucket
  `closed`.
- Main table REQUIRED columns beyond domain fields: `status` (open|closed),
  `disposition` (pending for open; 4-6 closed values), a month column formatted
  `2026-03`..`2026-06`, `age_days` (int), a deadline/runway int column, and
  `deadline_bucket` categorical (`0_X_days`/`Y_Z_days`/`over_Z_days`/`closed`) — buckets
  are how deadline charts avoid closed-row dilution (measures cannot combine filters
  with expr).
- Amounts/scores as plain decimals (bronze ingests strings; semantic layer casts).
- Encode 5-6 REAL domain patterns in the rows (fraud signatures, regulatory clocks,
  clusters) — the 6 open rows become the case queue and each note must cite row-level
  evidence by id (like card-disputes queue.yaml notes).

## Semantic model grammar (verified-safe subset — violations fail install)

- entities: `table: main.<dataset name with dashes→underscores>`, primary_key,
  `dataset_version_policy: { policy: latest }`.
- join_paths: left / many_to_one ONLY, acyclic (star or chain; both proven).
- dimensions: `type: categorical` ONLY (bronze is all-string; time dims fail).
- measures: `agg: count` plain or with `filters: "col = 'val'"` (single equality ONLY);
  numeric aggs MUST `expr: "cast(col as double)"` with `format: decimal`;
  ratios as separate `expr_metric: "a / nullif(b, 0)"` entries with `format: percent`
  (only `/` + nullif — no `+`).
- ~20-25 measures incl. 4-5 expr_metric ratios. Every dashboard measure/dimension must
  exist here AND have non-empty data.

## Queries

- verified.yaml: 5 entries {nl_text, sql_text, model, tags}. SQL uses
  `{{dataset('<kebab-name>')}}` macros ONLY (never raw table names). CASE WHEN sums,
  round(avg(cast(...))) patterns fine.
- saved.yaml: 2 entries {identity, name, description, sql, tags}. One MUST be a network
  edge source: `SELECT x AS parent, y AS child, count(*) AS n ... GROUP BY`.

## Dashboards (3 files, 5 charts each)

- chart_type ∈ {vertical_bar_chart, pie_chart, line_chart, grid_chart} ONLY.
- Never chart an expr_metric directly — rates are query/NL surfaces.
- Shape per chart exactly as card-disputes: config.x{dimension}, config.y[{measure,
  agg_fn}], sources[{measure}], w/h (6/4, grid 12/4). grid_chart adds config.columns.
- Line charts use the month dimension. Verify against your own CSV that every
  dimension value used has rows.

## Cases

- dispositions.yaml: 5 rows {code, label, category, requires_note: true}. category MUST
  be from the closed set {true_positive, false_positive, benign, inconclusive, other} —
  one each, mapped sensibly to the domain.
- queue.yaml: `dataset: <main dataset identity>`, `due_days: 3-5`, `rows:` = the 6 OPEN
  main-table rows: {row_pk, severity (high|medium), display_projection{...key fields,
  note}}. Notes are 2-4 line evidence-rich investigator briefings citing row ids.

## Roles (5)

Compose ONLY verbs that appear in `packs/card-disputes/rbac/roles.yaml` — copy those
five action lists nearly verbatim and rename the roles to domain personas:
an intake analyst (L1), an investigator/reviewer (L2 + query create/export), a
specialist (L1 + ingestion reads), an operations manager/approver (disposition.approve +
bulk + audit + experiment.promotion.approve — the ONLY approver), and a read-only
auditor (read + audit + usage + pipeline/experiment reads, NO case write).

## Agents

Two entries mirroring card-disputes/agents/configs.yaml: `case-triage` (persona, domain,
detailed instructions: evidence-first, cite row ids, regulatory clocks, one disposition
per case from your 5 codes, NEVER take the governed action autonomously — human
approves; disposition_hints listing the codes) and `analytics` (grounded in your
semantic model, cite measure names, never speculate beyond governed data).

## Memories

8-10 records {content, tags, confidence 0.85-0.98}. Real regulatory/methodological
reference material for the domain (deadlines, definitions, evidence standards,
typologies/patterns, record-keeping + AI-governance expectations). Only include
specific numbers/citations you are certain of; otherwise qualitative.

## Pipelines

Two: `{identity, name, algorithm: isolation_forest, mode: train, dataset: <detail>}`
and `{identity, name, algorithm: xgboost, mode: train, dataset: <main>}`.

## BRD (docs/brd/NN_<slug>_pack_BRD.md)

Match BRD 32's structure/length (~150 lines): header (Deliverable type/Publisher/
version/Horizon/Status "authored, install pending"), §1 Overview (Purpose / Why this
vertical / Business value / In scope / Out of scope), §2 Personas + 8 user stories,
§3 FRs (<PREFIX>-FR-001 manifest, -010 ontology deferred, -020 semantic KPI table,
-030.. agents proposal-mode, -080 connectors deferred, -090 regulatory guardrails,
-100 roles & case schemas), §4 Domain model & data (what materializes), §5 Business
rules (8, BR-1 always "no autonomous <governed action> — proposal mode + four-eyes"),
§6 Dependencies, §7 NFR table, §8 Acceptance criteria (install exit 0, 15/15 charts,
6-case queue, 5 differentiated roles, idempotent, closed disposition categories,
unmodified Core, deferred ledgered), §9 Out of scope / future.

## Self-verification before you finish (MANDATORY)

1. `cd /Users/girithammisetty/Projects//Datacern-ai/packs && ../deploy/e2e/.venv/bin/python -m packctl.cli validate <pack-name>` → must print "manifest ok".
2. Run a python csv check: FKs resolve, 6 open rows match queue row_pks, open⇒pending,
   closed⇒bucket closed, every charted dimension/filter value has ≥1 row, chargeback-
   style filtered measures non-zero where charted.
3. Confirm every dashboard measure+dimension exists in the semantic model.
DO NOT install the pack — the orchestrator installs centrally.

/**
 * Shared PLATFORM capability articles — the step-by-step "how each Core surface
 * works" guide, identical across every pack. Authored against the real UI routes
 * and governance rules (four-eyes, RBAC, proposal-mode). Persona-agnostic: each
 * article notes which personas typically use it, and pack overlays reference
 * these by slug.
 */
import type { HelpArticle } from "./types";

export const PLATFORM_ARTICLES: HelpArticle[] = [
  // ── Getting started ──────────────────────────────────────────────────────
  {
    slug: "getting-started",
    title: "Signing in and finding your way around",
    summary: "The layout, the sidebar, the use-case switcher, quick search, and notifications.",
    area: "getting-started",
    audience: "all",
    order: 1,
    related: ["worklist", "copilot", "notifications"],
    body: `
Welcome to Windrose. This is a **governed decision-intelligence** workspace: you
work cases, an AI copilot drafts recommendations, and a second person approves
anything that changes a record. Nothing the AI suggests is applied automatically.

## What you see after you sign in

- **Home** — your queue ("tightest clocks first"), items awaiting your approval,
  and the learning loop (how your corrections train the next model version).
- **Left sidebar** — your navigation, grouped into **Casework**, **Data**,
  **Machine Learning**, and **Insights**. You only see the sections your role
  allows, so your sidebar may be shorter than a colleague's.
- **Top bar** — the **use-case switcher** (top-left), and on the right: the
  **Copilot** button, the **notifications bell**, the light/dark toggle, and
  sign-out.

## Everyday moves

1. **Switch use case.** The name at the top-left (e.g. your team's workspace) is a
   dropdown. Use it to move between use cases you belong to, or create a new one.
2. **Search / jump anywhere.** Press <kbd>⌘K</kbd> (Mac) or <kbd>Ctrl K</kbd>
   (Windows) to open the command palette — jump to any page or search across your
   work without leaving the keyboard.
3. **Open the Copilot** from the top-right at any time to ask a question or run an
   assist on the case you're viewing.
4. **Watch the bell** for approvals, assignments, and status changes — it updates
   live, no refresh needed.

> **Tip:** if a menu item you expect is missing, it's almost always a permissions
> thing — your role doesn't include that capability. Ask your workspace admin.
`,
  },

  // ── Casework ─────────────────────────────────────────────────────────────
  {
    slug: "worklist",
    title: "Your worklist (the queue)",
    summary: "Find, filter, sort, and pick up the cases assigned to you or your team.",
    area: "casework",
    audience: "all",
    order: 1,
    related: ["case-cockpit", "getting-started"],
    body: `
Your **worklist** is where the day starts. Each row is a **case** — a durable
pointer to one record (a dispute, a claim, an appeal…) plus its workflow state.

## Open it

- Click **Cases** in the sidebar, or the **Full worklist →** link on Home.

## Read a row

Every row shows the case in your domain's language (not raw IDs): the subject,
its type/severity, the amount at stake, and a **clock** ("2d left") when a
regulatory or SLA deadline applies. Rows with the tightest clocks sort to the top.

## Narrow it down

1. Use the **filters** at the top of the list (status, type, assignee, severity).
2. Click a **column header** to sort; click again to reverse, once more to clear.
3. Change **rows per page** and page through with prev/next.

## Pick up work

- Click any row to open the **case cockpit** (see *Working a case*).
- Statuses update **live** — if a colleague takes a case or the copilot finishes
  a run, the row reflects it without a refresh.

> **Who uses this:** everyone. Analysts and investigators live here; managers use
> it to see the whole team's load and reassign.
`,
  },
  {
    slug: "case-cockpit",
    title: "Working a case: the decision cockpit",
    summary: "The case detail view — evidence, the recommendation, and recording a disposition.",
    area: "casework",
    audience: "all",
    order: 2,
    related: ["evidence", "copilot", "approvals", "worklist"],
    body: `
Opening a case gives you the **decision cockpit**: everything you need to decide,
on one screen, framed in your domain's language.

## What's on the screen

- **Header** — the case subject, type, severity, and any deadline clock.
- **The record** — the key fields for this case (the "display projection"), so you
  don't have to go hunting in a source system.
- **Recommendation** — if the copilot has run, its proposed disposition and its
  reasoning appear here as a **proposal** (never applied automatically).
- **Evidence / Attachments** — supporting documents on the case (see *Evidence*).
- **Activity** — the history of what's happened on the case.

## Record a decision (a "disposition")

1. Review the record and the evidence.
2. (Optional) Run or read the **Copilot** recommendation.
3. Choose a **disposition** from the list your pack defines (e.g. *Resolve in the
   customer's favor*, *Deny — no error found*, *Escalate*). Most require a **note**.
4. **Save.** Depending on the disposition and your pack's rules, this either
   records immediately or opens a **proposal** that a second person approves
   (see *Approvals & four-eyes*).

## Assign / reassign

- Use the **assignee** control to take the case yourself or hand it to a
  colleague. Managers can reassign from the worklist too.

> **Governance:** anything that writes back to a system of record goes through the
> proposal path — you propose, someone else approves. You can never both propose
> and approve the same change.
`,
  },
  {
    slug: "evidence",
    title: "Evidence and attachments",
    summary: "Attach, view, and download the documents that support a case.",
    area: "casework",
    audience: "all",
    order: 3,
    related: ["case-cockpit", "copilot"],
    body: `
Cases carry **evidence** — the documents that justify the decision (receipts,
letters, statements, reports). The AI can also reason over these when it drafts a
recommendation.

## Attach a document

1. On the case, open the **Attachments** (Evidence) tab.
2. Click **Attach** and choose a file (PDF, image, CSV/JSON/XML, etc.).
3. It uploads to secure object storage and is listed on the case with who added it
   and when.

## Use evidence

- **View / download** any attachment from the list.
- When you run the **Copilot**, it can read the attached documents (extract text
  from PDFs, parse structured files) and cite them in its reasoning — so its
  recommendation is grounded in the actual evidence, not guesses.

> **Who uses this:** investigators and specialists attach and cite evidence;
> auditors review it after the fact.
`,
  },
  {
    slug: "copilot",
    title: "The Copilot and AI agents",
    summary: "Ask questions and run an AI assist that drafts a recommendation — as a proposal you review.",
    area: "casework",
    audience: "all",
    order: 4,
    related: ["case-cockpit", "approvals", "evidence"],
    body: `
The **Copilot** is your AI assistant. On a case it can triage, summarize evidence,
and **draft a disposition with reasoning** — but it works in **proposal mode**: it
never changes a record on its own. You (and an approver) stay in control.

## Run an assist on a case

1. Open the case, then open the **Copilot** (top-right, or the on-case action).
2. Ask a question, or run the pack's assist (e.g. "triage this dispute").
3. The agent reads the case record and its **evidence**, applies your pack's
   grounding (regulatory rules, policies), and returns a **recommended
   disposition + rationale**.
4. Its recommendation lands as a **proposal** on the case and in the approvals
   inbox — nothing is applied yet.

## What the agent can and can't do

- It reads only data your workspace allows, within a per-agent **data scope** and
  **token budget**, and it **redacts** sensitive fields on the way out.
- It can propose actions but **cannot self-approve** — a human approves every
  write. This is the same four-eyes rule that applies to you.

## The learning loop

When you **correct** the AI's recommendation, that correction is captured and
becomes training data for the next model version — so the assist gets better at
your team's real decisions over time.

> **Who uses this:** intake analysts and investigators run it constantly;
> everyone benefits from the summaries.
`,
  },
  {
    slug: "approvals",
    title: "Approvals and four-eyes",
    summary: "Review proposed decisions in your inbox and approve or reject — you can't approve your own.",
    area: "casework",
    audience: "all",
    order: 5,
    related: ["case-cockpit", "copilot", "decision-tables"],
    body: `
Windrose is **governed**: any change that writes back to a record — a disposition,
a model promotion, an entity merge — is a **proposal** that a **second person**
approves. This is "four-eyes," and it's enforced by the platform, not by policy.

## Your approvals inbox

1. Click **Approvals** (or **Inbox**) in the sidebar — the badge shows how many
   items await you.
2. Open a proposal to see **what** will change, **who** proposed it, and the
   **reasoning / evidence** behind it.
3. **Approve** to apply the change to the system of record, or **Reject** with a
   note explaining why.

## The rules

- **You cannot approve your own proposal.** If you drafted or proposed it, the
  Approve button is blocked — a different person must review it. This is the
  self-approval guard, and it's absolute.
- Approving is what actually **writes back**. Until someone approves, the record
  is unchanged.
- Rejections carry a note and return the item for rework.

> **Who uses this:** the **operations manager / approver** role is the one that
> holds the approve capability (plus bulk actions and model-promotion approval).
> Analysts propose; the manager disposes.
`,
  },
  {
    slug: "decision-tables",
    title: "Decision tables (decision modeling)",
    summary: "Author governed if-this-then-that rules, publish them under review, and batch-evaluate.",
    area: "casework",
    audience: ["Dispute Operations Manager", "manager"],
    order: 6,
    related: ["approvals", "case-cockpit"],
    body: `
**Decision tables** capture the deterministic part of your policy as governed
rules — "if the amount is under X and the reason code is Y, recommend Z" — so
routine calls are consistent and explainable.

## Browse and author

1. Open **Decision Tables** in the sidebar.
2. Open a table to see its **rules** (conditions → outcome) and its version/status.
3. To change it, edit the draft: add/adjust rows, pick operators (=, <, ≥,
   contains, in…), and set the outcome per row.

## Lifecycle (governed)

A decision table moves **draft → in review → published**, and a **new published
version supersedes the old one** — the same four-eyes flow as everything else:

1. Edit the **draft**.
2. **Submit** it for review.
3. A **different** person **approves**, which publishes the new version. (You can't
   approve your own.)

## Try it before you ship it

- Use **batch evaluate** to run the draft table against a set of records and see
  what it would decide — so you catch surprises before publishing.

> **Who uses this:** the **operations manager** owns the policy tables; analysts
> see their outcomes as recommendations on cases.
`,
  },

  // ── Insights ─────────────────────────────────────────────────────────────
  {
    slug: "dashboards",
    title: "Dashboards and cross-filtering",
    summary: "Read your KPI dashboards, click a chart to filter the rest, and spin up cases from a chart.",
    area: "insights",
    audience: "all",
    order: 1,
    related: ["worklist", "datasets", "semantic-models"],
    body: `
**Dashboards** are your team's live KPI views — cardholder-favor rate, win rate,
deadline runway, exposure, and so on — built on governed semantic measures.

## Open and read

1. Click **Dashboards** in the sidebar and open one.
2. Charts render live from the warehouse. Each chart shows real, current numbers.

## Cross-filter by clicking

- **Click a bar or a slice** and the **other charts and grids on the dashboard
  filter to that value** — click the "auto" bar and everything narrows to auto.
- The chart you clicked stays showing all its categories (it never filters
  itself); a filter chip appears so you can see and clear active filters.
- This is a fast way to slice: pick a category in one chart, read the impact
  everywhere else.

## Turn a chart into work

- From a chart you can **create cases** for the records behind a segment — e.g.
  click a risk bucket and open a pre-filtered "create cases" step, so insight
  becomes a worklist in one move.

> **Who uses this:** managers and analysts for oversight; the specialist roles to
> spot patterns worth investigating.
`,
  },

  // ── Data ─────────────────────────────────────────────────────────────────
  {
    slug: "datasets",
    title: "Datasets: upload, browse, and quick-chart",
    summary: "Bring data in, explore rows, and chart a dataset without writing SQL.",
    area: "data",
    audience: ["datascientist", "Data Integration User"],
    order: 1,
    related: ["pipelines", "semantic-models", "entity-resolution"],
    body: `
**Datasets** are your governed tables in the warehouse. You can upload data,
browse it, and chart it — all under row-level tenant isolation.

## Upload a file

1. Go to **Data → Upload** (or the upload action on the Data page).
2. Choose a **CSV / JSON / Parquet / Avro / XML** file and follow the wizard
   (it detects the format and columns).
3. On completion the data lands as a **new dataset** backed by a real Iceberg
   snapshot in object storage.

## Browse rows

- Open a dataset's **Data** tab to page through rows server-side: sort by column,
  filter per column, and see total vs filtered counts. Large datasets stay fast
  because paging/sorting happens in the engine, not the browser.

## Quick-chart (no SQL)

- On a dataset's **Chart** tab, pick a **group-by** dimension, an **aggregate**
  (count/sum/avg/min/max), and a column, and it renders through the same chart
  engine the dashboards use — the aggregation runs safely in the query engine.

> **Who uses this:** data/integration roles bring data in; anyone can browse a
> dataset they're allowed to see.
`,
  },
  {
    slug: "pipelines",
    title: "Pipelines: train a model on your data",
    summary: "Instantiate a training template, point it at a dataset, run it, and get a real model.",
    area: "ml",
    audience: ["datascientist", "Model Builder"],
    order: 1,
    related: ["datasets", "ml-eval"],
    body: `
**Pipelines** turn a dataset into a trained model. You pick an algorithm template,
bind it to a dataset, run it, and the platform trains a real model and registers
it (logged to MLflow) — no code required.

## Build and run a training pipeline

1. Go to **Pipelines** and choose **New** (or instantiate from an **algorithm
   template** — logistic regression, random forest, XGBoost, k-means, and more).
2. Set **mode = train**, bind the **TRAIN** input to your **dataset**, and set the
   **label column** (the column the model should predict).
3. **Run** it. The run moves through **submitted → running → succeeded**, watchable
   live.
4. On success you get a **model URI**, **metrics** (accuracy, F1…), and a
   registered model version.

## Good to know

- The trainer reads your dataset's **real rows** — the train/test row counts add
  up to your uploaded rows, which is your proof it used your data.
- **Just uploaded the dataset?** Give it a moment to settle before training. A run
  fired in the same instant as ingestion can miss the brand-new dataset; if the
  first run fails with a "dataset not found," **retry** and it will pick it up.
- For useful accuracy, make sure numeric feature columns are typed as numbers
  (profile the dataset), not left as text.

> **Who uses this:** data-science / model-builder roles. The AI **ML-engineer
> agent** can also propose and run training for you — under the same approval
> rules.
`,
  },
  {
    slug: "semantic-models",
    title: "Semantic models",
    summary: "The governed definitions (dimensions and measures) that power dashboards and questions.",
    area: "data",
    audience: ["datascientist", "Model Builder"],
    order: 2,
    related: ["dashboards", "datasets"],
    body: `
A **semantic model** is the governed vocabulary over your data — the **dimensions**
(claim type, vendor…) and **measures** (count, total amount, win rate…) that
dashboards, charts, and the copilot all compile against. Define a metric once,
use it everywhere, consistently.

## Browse

1. Open **Semantic Models** in the sidebar.
2. The list shows each model and whether it's **published** (and which version).
3. Open a model to see its entities, dimensions, and measures.

## Author and publish (governed)

Models are versioned and reviewed like everything else:

1. Open or create a **draft** version and edit its definition (bind entities to
   datasets; define dimensions and measures).
2. **Submit** it for review — this validates the bindings.
3. A **different** person **approves**, which **publishes** the version and
   supersedes the previous one.

> **Who uses this:** data-science / model-builder roles author models; everyone
> consumes them through dashboards and the copilot.
`,
  },
  {
    slug: "entity-resolution",
    title: "Entity resolution",
    summary: "Unify duplicate records into one resolved entity — governed by four-eyes review.",
    area: "data",
    audience: ["datascientist", "Data Integration User"],
    order: 3,
    related: ["datasets", "approvals"],
    body: `
**Entity resolution** finds records that refer to the same real-world thing (the
same claimant, vendor, or member across spellings and systems) and unifies them —
so decisions are made on a complete picture, not a fragment.

## Run and review

1. Open **Entity Resolution** in the sidebar and start a **run** over a dataset.
2. It matches in two stages (exact + probabilistic) with blocking and thresholds,
   producing **stable clusters** and a list of **review candidates** (matches below
   the auto-merge line).
3. **Review candidates:** for each borderline pair, confirm or reject the match.
   Confirming a merge is a **proposal** — a **different** person approves it
   (four-eyes), and the source records are never mutated.

## Publish a resolved view

- **Materialize** a run to produce a governed **golden-record** dataset (one row
  per resolved entity, with rolled-up attributes) that dashboards, decision
  models, and agents can read as normal governed columns.

> **Who uses this:** data/integration roles run and steward resolution; a second
> reviewer approves merges.
`,
  },

  // ── Cross-cutting ────────────────────────────────────────────────────────
  {
    slug: "notifications",
    title: "Notifications and live status",
    summary: "The bell, delivery preferences, and why the UI updates without a refresh.",
    area: "getting-started",
    audience: "all",
    order: 2,
    related: ["getting-started", "approvals", "worklist"],
    body: `
Windrose keeps you current without refreshing. Two things drive that: the
**notification inbox** and **live status updates**.

## The bell

1. The **bell** in the top bar shows unread notifications — approvals waiting on
   you, cases assigned to you, runs that finished.
2. Open **Notifications** in the sidebar for the full inbox and to set your
   **delivery preferences** (what you get notified about, and how).

## Live status

- Lists and detail pages **patch themselves live**: when a case changes status, an
  ingestion completes, or a pipeline run finishes, the row/badge updates in place.
  You don't need to reload — if a number looks stale, it will catch up on its own.

> **Who uses this:** everyone. It's how the queue, the approvals badge, and run
> statuses stay honest.
`,
  },
];

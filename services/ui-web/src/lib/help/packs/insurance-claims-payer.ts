import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/insurance-claims-payer/). */
export const insuranceClaimsPayerGuide: PackGuide = {
  "packName": "insurance-claims-payer",
  "displayName": "Health Payer Claims",
  "summary": "AI-assisted **prior-authorization review, appeal analysis, and denial analytics** for US health payers. New PA requests and provider appeals land as cases with the **CMS decision clock** already running (72 hours for urgent, 7 days for standard), a domain-grounded copilot drafts a recommended disposition, and a qualified human reviewer approves every determination — because under the CMS 2024 MA rule an adverse decision can never be made by the AI alone.\n\nIt ships the payer KPI semantic models, three operational dashboards, PA/appeal review queues with a full disposition taxonomy, the payer role catalog, and a PA-approval training pipeline — grounded throughout in plan policy, clinical guidelines, and denial reason codes (CARC).",
  "ships": [
    {
      "label": "Review queues & decisions",
      "items": [
        "A seeded PA/appeal review worklist — pending prior-auths and provider appeals become OPEN cases with CMS-window clocks on day one",
        "Seven dispositions: PA approved, PA denied (human decision), PA deferred (more info), appeal upheld, appeal overturned, denial notice approved, escalate to medical director",
        "Six payer roles from PA Nurse Reviewer through Medical Director, Appeals Analyst, Payment Integrity Analyst, Compliance Officer, and Member Services"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "Two governed semantic models: payer_claims (denial rate, appeal overturn rate, billed/paid amounts) and payer_utilization (PA volumes, turnaround vs the CMS windows, agent recommendation mix)",
        "Three dashboards: Payer KPI, Appeals Analytics (overturns by denial reason), and PA Ops",
        "Seed claims, denials, appeals, and prior-auth datasets in the exact production landing shape"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A PA-nurse triage copilot and a payer-operations analytics agent, specialized to the domain",
        "Plan-policy, clinical-guideline, and CMS/CARC grounding memories",
        "A PA-approval classifier training pipeline (xgboost) on the prior-auth dataset"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "PA Nurse Reviewer",
      "tagline": "First touch — triage prior-auth requests and appeals against the CMS clock.",
      "usesCapabilities": [
        "getting-started",
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence"
      ],
      "steps": "You are the front line for utilization review: prior-authorization requests and provider appeals land in your queue, and the CMS decision clock (72 hours urgent, 7 days standard) is already running.\n\n1. **Find your way in.** New to the desk? The **Getting started** guide orients you to cases, the copilot, and how proposals become decisions. *(See \"Getting started\".)*\n2. **Open your queue.** Sidebar → **Cases**. The tightest clocks sort to the top — an urgent PA burning through its 72-hour window waits for no one. *(See \"Your worklist\".)*\n3. **Open a request.** You get the **decision cockpit**: requested service and CPT code, urgency tier, product type, member state, and the clock. *(See \"Working a case\".)*\n4. **Run the triage Copilot.** It reads the case, applies the plan-policy and clinical-guideline grounding, cites the denial reason code (CARC) where one exists, and drafts a recommended disposition — as a **proposal**. It will never recommend denying care on its own. *(See \"The Copilot\".)*\n5. **Attach evidence.** Clinical notes, prior-auth criteria, or the provider's appeal packet ground the recommendation in the real documents. *(See \"Evidence\".)*\n6. **Record your disposition.** Propose *PA approved*, *PA deferred — more info needed*, or for anything short of clear medical necessity, *Escalate to medical director*. Denials and deferrals require a note.\n7. **Hand off.** Your disposition becomes a **proposal** the Medical Director approves — you can't approve your own adverse determination, which is exactly what the CMS 2024 human-review rule requires."
    },
    {
      "roleName": "Medical Director",
      "tagline": "Own the clinical decision — approve determinations and clear escalations.",
      "usesCapabilities": [
        "approvals",
        "case-cockpit",
        "worklist",
        "dashboards",
        "copilot"
      ],
      "steps": "You hold clinical authority on the desk. You're the one who holds **approve**, so a determination becomes real only when you sign it — the human reviewer the CMS 2024 MA rule requires for every adverse decision.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, the reviewer who proposed it, and the copilot's reasoning and cited criteria. **Approve** to write it back or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Take the escalations.** Filter **Cases** to *Escalate to medical director* — the below-threshold PAs and specialty-drug denials the nurses sent up. *(See \"Your worklist\".)*\n3. **Decide on the cockpit.** Review the clinical picture and evidence, use the Copilot to summarize plan policy and precedent, then record *PA approved*, *PA denied (human decision)*, or send an appeal to *Appeal upheld* / *Appeal overturned*. *(See \"Working a case\" and \"The Copilot\".)*\n4. **Watch turnaround.** The **PA Ops** dashboard shows queue by urgency and average decision hours against the CMS windows — reassign from the **worklist** before anything breaches. *(See \"Dashboards\".)*\n5. **Clear backlogs.** When a batch of routine standard PAs is clean, bulk-approve to keep the queue moving without letting urgent cases wait."
    },
    {
      "roleName": "Appeals Analyst",
      "tagline": "Work provider appeals — build the medical-necessity case for overturn or upheld.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You work the appeals lane: provider disputes of denied claims, where an overturn means paying the claim and an upheld denial has to stand up to scrutiny.\n\n1. **Pick up appeals.** Filter **Cases** to appeal rows — each carries the denial reason code, the disputed CPT, and the potential value at stake. *(See \"Your worklist\".)*\n2. **Build the record on the cockpit.** Review the original denial, the claim, and the provider's **evidence**; the Copilot grounds your read in plan policy and pulls the relevant CARC. *(See \"Working a case\" and \"Evidence\".)*\n3. **Spot the pattern.** Open the **Appeals Analytics** dashboard — the overturns-by-denial-reason grid flags over-denied procedures where precedent favors the provider. *(See \"Dashboards\".)*\n4. **Record your disposition.** Propose *Appeal overturned — approve claim* when medical necessity is met, or *Appeal upheld — denial stands* when it isn't. Both require a note — it's your rationale of record.\n5. **Escalate** anything that turns on a clinical judgment call to *Escalate to medical director* rather than deciding it yourself.\n6. **Hand off.** Your disposition becomes a **proposal** a second reviewer approves — the four-eyes check on every appeal outcome."
    },
    {
      "roleName": "Payment Integrity Analyst",
      "tagline": "Hunt denial and payment patterns across the book — build the dashboards that surface them.",
      "usesCapabilities": [
        "dashboards",
        "semantic-models",
        "datasets",
        "case-cockpit"
      ],
      "steps": "You look across the whole book, not one case: where denials cluster, where paid amounts drift, where the appeal-overturn signal points to a policy that's over-denying.\n\n1. **Start from the KPIs.** Open the **Payer KPI** dashboard — claim volumes and paid amounts by product and service line, denial mix by reason code. *(See \"Dashboards\".)*\n2. **Query the governed models.** Build charts and grids on the **payer_claims** and **payer_utilization** semantic models — denial rate, appeal overturn rate, first-pass yield, turnaround — so every number traces to a defined measure. *(See \"Semantic models\".)*\n3. **Go to the source rows.** Browse the seed claims, denials, and appeals **datasets** to validate an anomaly before you flag it, and export the slice for a deeper look. *(See \"Datasets\".)*\n4. **Trace it to cases.** When a pattern points at specific requests, open them on the **cockpit** (read-only for you) to confirm what actually happened. *(See \"Working a case\".)*\n5. **Publish the view.** Turn a confirmed signal into a saved chart or dashboard the medical directors and analysts watch — so an over-denied procedure gets caught operationally, not just in an audit."
    },
    {
      "roleName": "Compliance Officer",
      "tagline": "Read-only oversight — verify every determination was human-reviewed and evidenced.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "approvals",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk honored the CMS human-review rule, HIPAA, and the NAIC AI bulletin — that no adverse determination was made by the AI alone and every decision is evidenced.\n\n1. **Review decided cases.** Open **Cases** and inspect closed PAs and appeals: the disposition, the required **note**, who proposed it, and who approved it. *(See \"Working a case\".)*\n2. **Prove four-eyes.** Confirm on each determination that the proposer and the approving reviewer are **different people** — the human-review check the CMS 2024 rule demands. *(See \"Approvals & four-eyes\".)*\n3. **Check the evidence trail.** Verify the clinical criteria and appeal documents are attached and were cited by the copilot before the decision. *(See \"Evidence\".)*\n4. **Monitor at scale.** Use the **PA Ops** and **Appeals Analytics** dashboards to watch turnaround against the CMS windows and overturn concentration for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Rely on the audit log.** Every proposal, approval, and edit is captured in the tamper-evident audit log, which your admin can stream to your SIEM.\n\n> You can see everything and change nothing — that's the point."
    },
    {
      "roleName": "Member Services",
      "tagline": "Answer member questions accurately — read the case, ask the copilot, never decide.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "notifications"
      ],
      "steps": "You are the member-facing voice. When a member calls about a prior-auth or a denied claim, you need the facts fast — without ever touching the determination itself.\n\n1. **Find the case.** Open **Cases** and locate the member's PA request or appeal. *(See \"Your worklist\".)*\n2. **Read the status.** The **cockpit** shows where the request sits — pending, approved, denied, or escalated — and the disposition once it's decided. *(See \"Working a case\".)*\n3. **Ask the Copilot.** Use it to plain-language what a denial reason code means or where a request is in the CMS window, so you can explain it clearly. It grounds answers in plan policy — no guessing. *(See \"The Copilot\".)*\n4. **Stay current.** Watch the **bell** for status changes on cases you're following, so you can proactively update a waiting member. *(See \"Notifications\".)*\n5. **Route, don't rule.** You have no disposition or approve rights — anything needing a decision goes to the review queue for a clinical reviewer."
    }
  ]
};

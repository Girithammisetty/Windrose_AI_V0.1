import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/pharmacovigilance/). */
export const pharmacovigilanceGuide: PackGuide = {
  "packName": "pharmacovigilance",
  "displayName": "Pharmacovigilance (ICSR Safety)",
  "summary": "\nAI-assisted **pharmacovigilance case processing** for pharma and biotech drug-safety departments. It handles individual case safety report (**ICSR**) intake — validity and duplicate checks, then **seriousness / expectedness / causality**-grounded reporting decisions — with **expedited-deadline (regulatory clock) tracking** and QPPV-equivalent approval on anything that becomes a regulatory submission. Grounded in FDA 21 CFR 314.80 / 312.32, ICH E2A/E2D, EU GVP and MedDRA.\n\nIt ships the dashboards, safety-operations semantic model, product–event signal analytics, regulatory grounding memories, and AI copilots to run the whole safety desk — while enforcing that **no report is ever submitted autonomously**.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded ICSR worklist (queue) with expedited-deadline clocks",
        "Five dispositions: submit expedited report (QPPV approves), include in periodic report (PADER/PSUR), non-reportable (valid-case criteria not met), request follow-up information, nullify as duplicate"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"pv_core\" safety-operations semantic model (serious-case rate, expedited-conversion rate, unlisted-event signal pressure, duplicate rate, backlog aging, deadline runway)",
        "Three dashboards: PV Case Operations, Regulatory Reporting, Signal Watch",
        "Product–event signal network analytics, plus verified and saved canonical questions"
      ]
    },
    {
      "label": "AI, models & grounding",
      "items": [
        "An ICSR-triage copilot and a PV analytics agent (domain-specialized, always proposal-mode)",
        "FDA / ICH / GVP and MedDRA regulatory grounding memories",
        "Event-anomaly (isolation forest) and case-priority (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "PV Intake Specialist",
      "tagline": "First touch — validate incoming ICSRs, screen duplicates, start the regulatory clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: new adverse-event reports (ICSRs) land in your queue, and the expedited-reporting clock starts ticking the moment a case is valid.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadlines sort to the top — a serious, unexpected case has a hard regulatory clock. *(See \"Your worklist\".)*\n2. **Open a case.** You get the **decision cockpit**: reporter, patient, suspect product, event term(s), onset, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the intake Copilot.** It works the case in order — checks the four **validity** criteria (identifiable reporter, patient, suspect product, event), then screens for **duplicates** against processed cases, applying the FDA/ICH/GVP grounding, and drafts a recommended disposition as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence.** Add the source intake document, literature, or reporter correspondence so the recommendation is grounded in the real record. *(See \"Evidence\".)*\n5. **Record your disposition.** If a minimum criterion is missing, propose *Request follow-up information* (a case is never dismissed while follow-up is pending); if it matches an existing case, *Nullify — duplicate* with the case id cited; otherwise route it onward. Add the required note.\n6. **Hand off.** Serious/unexpected cases go to medical review and ultimately the Safety Officer — you propose, you don't approve your own. Watch the **bell** for assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "PV Medical Reviewer",
      "tagline": "Own the medical assessment — seriousness, expectedness against the RSI, and causality.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "datasets"
      ],
      "steps": "You own the clinical judgment on each case: seriousness, expectedness, and causality — the assessment the report narrative will cite.\n\n1. **Pick up cases for review.** Filter **Cases** to your assignments. *(See \"Your worklist\".)*\n2. **Assess on the cockpit.** Confirm **seriousness** per ICH E2A (death, life-threatening, hospitalization, disability, congenital anomaly, or other medically important), **expectedness** against the product's reference safety information (an unlisted term is unexpected), and **causality** using dechallenge/rechallenge, temporal plausibility, and alternative etiologies. *(See \"Working a case\".)*\n3. **Lean on the Copilot and evidence.** Have it summarize the case, cite the attached source documents and grounding, and surface the specific criterion driving seriousness — then verify it yourself. When arguable, assess conservatively (serious/unexpected) and say why. *(See \"The Copilot\" and \"Evidence\".)*\n4. **Check the underlying data.** Where you need the coded event book behind a case, inspect the seeded **datasets** and their lineage. *(See \"Datasets\".)*\n5. **Record the reporting decision.** Propose *Submit expedited report* for serious + unexpected + reasonable causality (noting which clock applies), or *Include in periodic report* when reportable but not expedited. Your note carries the full assessment.\n6. **Hand off for approval.** Your proposal goes to the Safety Officer — the submission decision is never yours to finalize alone."
    },
    {
      "roleName": "PV Safety Officer",
      "tagline": "QPPV-equivalent — the one who approves any disposition that becomes a regulatory submission.",
      "usesCapabilities": [
        "approvals",
        "case-cockpit",
        "dashboards",
        "worklist",
        "notifications"
      ],
      "steps": "You are the QPPV-equivalent. Separation of duties routes every submission decision to you: proposals become real regulatory outcomes only when you approve.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the seriousness/expectedness/causality reasoning and evidence behind it. **Approve** to commit, or **Reject** with a note. You **can't** approve a proposal you authored — that's the four-eyes control. *(See \"Approvals & four-eyes\".)*\n2. **Guard the deadlines.** The **Regulatory Reporting** dashboard shows deadline runway on the open book and expedited filings over time — reassign from the **worklist** so nothing breaches its expedited clock. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Watch operations and signal.** The **PV Case Operations** and **Signal Watch** dashboards show backlog aging, reporting-decision mix, and unlisted-term signal pressure — your early warning on both compliance and safety. *(See \"Dashboards\".)*\n4. **Sit on the case.** When a decision needs your eyes directly, open it on the **decision cockpit** to review the full assessment and audit trail. *(See \"Working a case\".)*\n5. **Approve model promotions too.** You also hold approval on promoting the pack's trained scoring models — nothing goes to production scoring without your sign-off. Keep the **bell** on for approaching deadlines. *(See \"Notifications\".)*\n\n> No report is ever filed autonomously — the submission decision is yours, and it's logged."
    },
    {
      "roleName": "PV Signal Analyst",
      "tagline": "Work the aggregate surfaces — spot product–event signals across the whole case book.",
      "usesCapabilities": [
        "dashboards",
        "semantic-models",
        "datasets",
        "pipelines",
        "copilot"
      ],
      "steps": "You work above the individual case: the aggregate view where a cluster of unlisted terms on one product becomes a signal.\n\n1. **Open Signal Watch.** Review events by system organ class, the serious-case trend, unlisted events by preferred term, and the product signal grid. **Click** a segment to cross-filter the rest — a fast way to isolate a product or organ class worth a closer look. *(See \"Dashboards\".)*\n2. **Ask the questions directly.** Use the PV analytics Copilot over the governed **pv_core** semantic model for KPI questions — serious-case rate, unlisted-event share, duplicate rate, backlog aging, deadline runway — always cited back to the model. *(See \"The Copilot\" and \"Semantic models\".)*\n3. **Profile the raw event book.** Inspect and profile the seeded **datasets** behind the signal surfaces, following lineage where you need it. *(See \"Datasets\".)*\n4. **Read the model output.** Review runs of the **event-anomaly** and **case-priority** training pipelines — the quantitative signal-screening and intake pre-router seeds. *(See \"Pipelines\".)*\n5. **Route what you find.** A confirmed cluster goes back to medical review and the Safety Officer alongside the individual-case decisions — you surface the signal, the case desk acts on it."
    },
    {
      "roleName": "PV Quality Auditor",
      "tagline": "Read-only inspection readiness — verify every decision was made, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for inspection readiness. Your job is to confirm the desk followed FDA/ICH/GVP and that every decision is evidenced and four-eyed — with no case-write power of your own.\n\n1. **Review processed cases.** Open **Cases** and inspect each decision: the disposition, the required **note** carrying the seriousness/expectedness/causality assessment, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Your worklist\" and \"Working a case\".)*\n2. **Check the evidence trail.** On each case, confirm the source documents are attached and were cited in the assessment. *(See \"Evidence\".)*\n3. **Confirm submission control.** Verify that every *Submit expedited report* was approved by the Safety Officer — never self-approved. *(See \"Approvals & four-eyes\".)*\n4. **Monitor at scale.** Use the **PV Case Operations** and **Regulatory Reporting** dashboards to watch serious-case rate, duplicate rate, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Verify model governance.** Review the training **pipelines**, their runs, and promotion history for GxP/CSV documentation expectations. *(See \"Pipelines\".)*\n6. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's inspection readiness by design."
    }
  ]
};

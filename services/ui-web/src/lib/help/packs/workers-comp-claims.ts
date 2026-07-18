import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/workers-comp-claims/). */
export const workersCompClaimsGuide: PackGuide = {
  "packName": "workers-comp-claims",
  "displayName": "Workers' Comp Claims",
  "summary": "\nAI-assisted **workers' compensation claims adjudication** for US carriers, TPAs, and self-insured employers. It handles FNOL triage with **statutory decision-clock awareness** (state accept/deny/delay windows), **AOE/COE compensability** grounding, **SIU fraud** red-flag escalation (Monday-morning / unwitnessed / post-layoff clusters), and **medical bill review** against state fee schedules — plus reserve-adequacy and return-to-work tracking.\n\nIt ships the dashboards, semantic model, grounding memories, and AI agents to run the whole desk. Every final determination — compensability, denial, reserve change — is a **proposal** a second person approves; no autonomous benefit decision, payment, or state filing, ever.\n",
  "ships": [
    {
      "label": "Case queue & determinations",
      "items": [
        "A seeded claim worklist (queue) with the statutory decision clock front and center",
        "Five dispositions: accept — compensable (AOE/COE established), deny — compensability not established, refer to utilization / bill review, escalate to SIU (fraud red flags), close — successful return to work",
        "Every determination routed to a human adjuster, with the WC Claims Manager as the four-eyes approver"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"wc_claims_core\" semantic model (acceptance/denial rate, SIU referral share, litigation rate, fee-schedule variance, deadline runway, reserve exposure, claim-age backlog)",
        "Three dashboards: WC Claims Command Center, Compensability Clock & Reserves, Medical Bill Review",
        "Seed datasets in the real landing shape plus verified/saved analytics questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A claims-triage copilot and a claim-operations analytics agent, both proposal-mode",
        "State-WC-law, fee-schedule, MSP, and NAIC AI-governance grounding memories",
        "Two training pipelines: medical-bill anomaly detector (isolation forest) and compensability-outcome scorer (xgboost)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "WC Claims Adjuster",
      "tagline": "First touch — triage new injury claims and beat the statutory decision clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: new FNOLs land in your queue, and the state's accept/deny decision window starts running the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest statutory clocks sort to the top — a lapsed deadline is a pay-or-dispute problem you never want. *(See \"Your worklist\".)*\n2. **Open a claim.** You get the **decision cockpit**: claimant, employer, injury mechanism, body part, jurisdiction state, and the days-to-deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the claim and its **evidence**, applies the AOE/COE and state-law grounding, and drafts a recommended disposition with reasoning — as a **proposal**. It watches the clock first and will flag a claim at risk of the decision window. *(See \"The Copilot\".)*\n4. **Ground it in the file.** Attach the FNOL report, medical records, or witness statements so the recommendation cites the real documents, not a guess. *(See \"Evidence\".)*\n5. **Record your disposition.** *Accept — compensable* when AOE/COE is established; *Deny — compensability not established* with the specific findings the denial notice will cite; *Escalate to SIU* on a red-flag cluster; *Refer to utilization / bill review* for medical-necessity questions. A note is required on every one.\n6. **Hand off.** Your determination becomes a **proposal** the WC Claims Manager approves — you can't approve your own. Watch the **bell** for assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "WC Nurse Case Manager",
      "tagline": "Own medical management and return-to-work — keep treatment and RTW on track.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "dashboards"
      ],
      "steps": "You manage the medical side: treatment trajectory, escalations, and getting the claimant safely back to work.\n\n1. **Work your assignments.** Filter **Cases** to claims assigned to you or in active treatment. You can **assign** and advance cases, so you keep the medical workflow moving. *(See \"Your worklist\".)*\n2. **Review on the cockpit.** Read the injury detail, treatment history, and the **evidence** — medical records, IME reports, work-status notes. Use the Copilot to summarize the file and surface causation or reserve concerns. *(See \"Working a case\" and \"Evidence\".)*\n3. **Track the runway.** Open the **Compensability Clock & Reserves** dashboard to see decision-deadline runway and reserve exposure by jurisdiction; a fast-rising reserve is your cue to flag for management. *(See \"Dashboards\".)*\n4. **Update the disposition.** When the claimant is back at work and treatment has concluded, propose *Close — successful return to work* with the RTW note; route medical-necessity or fee questions with *Refer to utilization / bill review*.\n5. **Escalate cleanly.** On litigated claims all claimant contact goes through counsel — keep your notes precise; they're the audit trail. Determinations still flow to the WC Claims Manager for approval."
    },
    {
      "roleName": "WC Medical Bill Reviewer",
      "tagline": "Guard the fee schedule — catch above-schedule billing and provider-cluster patterns.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "datasets",
        "evidence"
      ],
      "steps": "You own bill review: every medical bill checked against the state fee schedule, with an eye for provider clusters and repeat-procedure patterns.\n\n1. **Pick up bill-review work.** Filter **Cases** to claims referred to utilization / bill review. *(See \"Your worklist\".)*\n2. **Read the bills on the cockpit.** Confirm provider type, service category, billed vs. paid amounts, and the fee-schedule flag against the **evidence** attached to the case. The Copilot cites the bill ids and the schedule variance. *(See \"Working a case\" and \"Evidence\".)*\n3. **Hunt patterns.** Open the **Medical Bill Review** dashboard and **click** a segment — a provider type or service category — to cross-filter the rest and see where above-schedule billing concentrates. *(See \"Dashboards\".)*\n4. **Check the feeds.** You have read access to the ingestion connections and the seeded bill datasets, so you can confirm a bill traces back to a real inbound record. *(See \"Datasets\".)*\n5. **Disposition and note.** Propose the bill-review outcome with the variance findings; questionable clusters get flagged toward *Escalate to SIU*. Your reads feed the bill-anomaly model, so the desk gets sharper over time. Determinations go to the WC Claims Manager for approval."
    },
    {
      "roleName": "WC Claims Manager",
      "tagline": "Run the desk — approve compensability determinations, watch the clocks, promote the models.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the desk. You're the one who holds **approve**, so a determination becomes real only when you say so — compensability decisions and denials are four-eyes by design.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the statutory clocks.** The **Compensability Clock & Reserves** dashboard shows decision-deadline runway and reserve exposure across the open book — reassign from the **worklist** so nothing lapses the accept/deny window. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Run the command center.** The **WC Claims Command Center** tracks disposition mix, backlog aging, and intake trend so you can spot where the desk is falling behind. *(See \"Dashboards\".)*\n4. **Balance the load.** Reassign across intake, nurse case management, and bill review from the worklist to keep any queue from backing up. *(See \"Working a case\".)*\n5. **Govern the models.** When a retrained compensability or bill-anomaly model is ready, you review and approve its promotion — the same governed, human-approved gate applies to the models as to the claims. *(See \"Pipelines\".)*"
    },
    {
      "roleName": "WC Compliance Auditor",
      "tagline": "Read-only oversight — verify every determination was decided, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for state-audit and reinsurer readiness. Your job is to confirm the desk followed the compensability law and fair-claims-handling expectations — and that every determination is evidenced and four-eyed.\n\n1. **Review resolved claims.** Open **Cases** and inspect closed determinations: the disposition, the **note**, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Verify denials carry findings.** Confirm every *Deny — compensability not established* cites the specific AOE/COE findings its denial notice would — and that SIU referrals never silently lapsed a statutory deadline. *(See \"Working a case\".)*\n3. **Monitor at scale.** Use the dashboards to watch acceptance/denial rate, SIU referral share, litigation rate, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the models.** Review the training pipeline runs and model lineage behind the scoring — how a model was built and evaluated is part of the audit story. *(See \"Pipelines\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

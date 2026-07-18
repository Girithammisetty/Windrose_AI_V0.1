import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/credit-disputes/). */
export const creditDisputesGuide: PackGuide = {
  "packName": "credit-disputes",
  "displayName": "Credit-Reporting Disputes",
  "summary": "AI-assisted **FCRA credit-reporting dispute** investigation for furnishers and consumer reporting agencies (banks and lenders as furnishers, CRAs, debt buyers/collectors, fintech servicers). Unlike card disputes — which decide whether a *charge* stands — this pack governs what gets *reported* about a consumer: **FCRA 611 reinvestigations** with regulatory-clock awareness (the 30/45-day window, e-OSCAR ACDV response deadlines, and the 605B identity-theft block on a 4-business-day clock), reasonable-investigation depth beyond bare data-matching, obsolescence and duplicate-tradeline detection, and documented frivolous-dispute handling.\n\nIt ships the dashboards, dispositions, AI agents, grounding memories, and training pipelines to run a dispute-operations desk — every correction, deletion, block, or frivolous notice stays **proposal-mode with a second-person approval** (four-eyes), so nothing that changes a consumer's file is ever furnished autonomously.",
  "ships": [
    {
      "label": "Case queue & determinations",
      "items": [
        "A seeded reinvestigation queue with FCRA regulatory clocks (611 window, ACDV deadline, 605B 4-business-day block)",
        "Five dispositions: correct tradeline (furnish to all CRAs), verify accurate as reported, delete unverifiable/obsolete, escalate to identity-theft (605B) review, close frivolous — documented notice",
        "Every determination is proposal-mode with the Dispute Operations Manager as the four-eyes approver"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"credit_disputes_core\" semantic model (correction rate, verified-accurate rate, deletion rate, identity-theft share, deadline runway, channel mix)",
        "Three dashboards: Dispute Operations Center, FCRA Clock & Channels, Accuracy & Outcomes",
        "Verified and saved canonical questions for consumer/creditor reporting analytics"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A reinvestigation triage copilot and a dispute-operations analytics agent, specialized to the FCRA/Reg V domain",
        "FCRA / Reg V / CFPB grounding memories (reasonable-investigation depth, obsolescence, frivolous notice)",
        "Two training pipelines: a furnished-tradeline anomaly detector (isolation forest) and a dispute-outcome scorer (xgboost)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Dispute Investigator",
      "tagline": "Work the reinvestigation queue — triage disputes and propose a determination.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of FCRA reinvestigation: disputes land in your queue, and the 611 clock is already running when they do.\n\n1. **Open the reinvestigation queue.** Sidebar → **Cases**. The tightest regulatory clocks sort to the top — a dispute nearing its 30/45-day window or ACDV deadline waits for no one. *(See \"Your worklist\".)*\n2. **Open a dispute.** You get the **decision cockpit**: the consumer, the tradeline, the furnisher/CRA, the dispute reason and channel, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any attached **evidence**, applies the FCRA/Reg V grounding, and drafts a recommended disposition with record-level reasoning — as a **proposal**, never a furnished change. *(See \"The Copilot\".)*\n4. **Do a reasonable investigation.** Check the underlying record — application, servicing ledger, chain of assignment — not just a name/SSN re-match. Attach or cite the **evidence** so the recommendation is grounded in the real documents. *(See \"Evidence\".)*\n5. **Record your determination.** Propose *Correct tradeline* when the record shows an accuracy error, *Verify accurate as reported* when it substantiates the reporting, *Delete — unverifiable or obsolete* when it can't be verified or is past the reporting period, or *Escalate to identity-theft review* for a 605B claim. Every disposition requires a note.\n6. **Hand off.** Your determination becomes a **proposal** the Dispute Operations Manager approves — you can't approve your own. Watch the **bell** for new assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Furnisher Data Analyst",
      "tagline": "Dig into the furnished reporting data — author queries, profile the book, export for exams.",
      "usesCapabilities": [
        "datasets",
        "semantic-models",
        "dashboards",
        "worklist",
        "case-cockpit"
      ],
      "steps": "You go deep on the reporting data behind the disputes — the furnished tradeline book, its lineage, and the patterns that decide whether an item is accurate.\n\n1. **Explore the data.** Sidebar → **Datasets**. Browse the furnished tradelines and disputes, read the **profile** and **lineage** so you know where each reported field came from before you touch a case. *(See \"Datasets\".)*\n2. **Author governed queries.** Ask questions against the **credit_disputes_core** semantic model — correction rate by furnisher system, duplicate-collection tradelines, re-aged delinquencies — so every number is defined once and consistent. *(See \"Semantic models\".)*\n3. **Confirm on the dashboards.** Cross-check your findings on **Accuracy & Outcomes** (corrections by tradeline type, verified-accurate by reason) and **Dispute Operations Center**; click a segment to cross-filter the rest. *(See \"Dashboards\".)*\n4. **Work and enrich cases.** You can pick up, assign, and update disputes from the **worklist** and cockpit — pulling the query evidence you found straight into the determination. *(See \"Your worklist\" and \"Working a case\".)*\n5. **Export for the record.** Export query results and evidence for furnisher-accuracy oversight and CFPB exam prep. Any determination you propose still goes to the Manager for four-eyes approval."
    },
    {
      "roleName": "Identity Theft Specialist",
      "tagline": "Own the 605B block track — verify identity-theft claims against a 4-business-day clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "notifications"
      ],
      "steps": "You own the identity-theft lane: disputes escalated as *not mine* or with an FTC IdentityTheft.gov report, where a valid 605B block runs on a strict 4-business-day clock.\n\n1. **Pick up the block track.** Filter **Cases** to identity-theft escalations. These clocks are the tightest on the desk — sort by deadline runway. *(See \"Your worklist\".)*\n2. **Verify on the cockpit.** Confirm the identity-theft report and proof of identity, review the tradeline and account status, and read the **evidence** attached to the case. *(See \"Working a case\" and \"Evidence\".)*\n3. **Check the incoming feeds.** You have read access to the ingested FTC-report and ACDV feeds — use them to confirm the claim is real and current, not a duplicate or a serial-disputer repeat.\n4. **Let the Copilot ground the call.** It cites the record and the 605B / reasonable-investigation grounding so your recommendation stands up to review — as a **proposal**. *(See \"The Copilot\".)*\n5. **Propose the outcome.** *Escalate to identity-theft review* to apply the block, or send it back toward *Verify accurate* or *Delete* when the claim doesn't hold. A note is required, and the Manager approves the block — you propose, you don't apply it yourself. Keep the **bell** open for clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Dispute Operations Manager",
      "tagline": "Own the desk — approve every determination, watch the clocks, govern promotions.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines",
        "notifications"
      ],
      "steps": "You run the reinvestigation desk. You alone hold **approve**, so a correction, deletion, 605B block, or frivolous notice becomes real only when you sign off — that's the four-eyes wall.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence. **Approve** to let it be furnished, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **FCRA Clock & Channels** dashboard shows deadline runway across the open book and the identity-theft block mix — reassign from the **worklist** to keep any 611 window, ACDV deadline, or 605B clock from breaching. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Balance the load.** Use the **Dispute Operations Center** (backlog aging by severity, determination outcome mix) to spot bottlenecks and rebalance across investigators, the data analyst, and the identity-theft track. *(See \"Working a case\".)*\n4. **Govern the models.** The furnished-tradeline anomaly and dispute-outcome pipelines feed triage; you review and **approve promotions** before any trained model informs the desk. *(See \"Pipelines\".)*\n5. **Stay ahead of exams.** Keep the **bell** on for deadline-risk and approval alerts, and export dashboards for furnisher-accuracy oversight. *(See \"Notifications\".)*"
    },
    {
      "roleName": "FCRA Compliance Auditor",
      "tagline": "Read-only oversight — confirm every determination was reasonable, evidenced, and four-eyed.",
      "usesCapabilities": [
        "case-cockpit",
        "dashboards",
        "datasets",
        "semantic-models",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for exam readiness. Your job is to confirm the desk ran reasonable investigations under FCRA 611/623(b) and that every determination is evidenced and four-eyed — you change nothing.\n\n1. **Review closed reinvestigations.** Open **Cases** and inspect the disposition, the required **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check investigation depth.** Confirm each determination cites the underlying record — not a bare name/SSN re-match — and that frivolous closures carry the documented notice with reasons. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use **Accuracy & Outcomes** and **FCRA Clock & Channels** to watch correction rate, verified-accurate rate, deletion rate, identity-theft share, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the lineage.** Read **Datasets** profiles/lineage and the **credit_disputes_core** semantic model to verify the numbers trace back to governed data. *(See \"Datasets\" and \"Semantic models\".)*\n5. **Inspect the models.** Review the training pipelines and their runs to confirm scoring models were governed and promotion-approved before use. *(See \"Pipelines\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/background-screening/). */
export const backgroundScreeningGuide: PackGuide = {
  "packName": "background-screening",
  "displayName": "Background Screening",
  "summary": "\nAI-assisted **employment background-screening adjudication** for consumer reporting agencies (CRAs), employer talent-compliance teams, and gig platforms. It handles screening-hit triage with **FCRA accuracy and obsolescence awareness** (the 7-year rule, the 607(b) maximum-possible-accuracy bar, 613 public-record rails), **identity resolution** for common-name and mixed-file hits, and the pre-adverse / adverse-action **two-step clock** — with EEOC individualized-assessment routing on borderline convictions.\n\nOn top of the case work it ships a screening-operations **semantic model and dashboards** (clear rate, adverse-finding rate, suppression rate, identity-escalation share, deadline runway, turnaround), FCRA/EEOC **grounding memories**, and training pipelines — all reusing the same platform surfaces every Windrose pack runs on.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded screening-adjudication worklist (queue) with pre-adverse / adverse-action clocks",
        "Five dispositions: clear (report-eligible), report adverse finding, suppress (not reportable), request identity verification, route to individualized assessment (EEOC Green factors)",
        "Every reportable adverse finding and final determination routed to a human adjudicator with the Operations Manager as four-eyes approver"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"screening_core\" semantic model (clear rate, adverse-finding rate, suppression/accuracy-save rate, identity-escalation share, deadline runway, turnaround)",
        "Three dashboards: Screening Adjudication Center, Adverse Action Clocks, Accuracy & Identity Watch",
        "Applicant–employer network analytics and verified/saved canonical questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A hit-adjudication triage copilot and a screening-operations analytics agent (proposal-mode, four-eyes)",
        "FCRA / EEOC grounding memories (obsolescence, 607(b) accuracy, job-relatedness)",
        "Order-anomaly (isolation forest) and hit-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Screening Adjudicator",
      "tagline": "First touch — triage screening hits and propose the FCRA disposition.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: new screening hits land in your queue, and the pre-adverse / adverse-action clock is already running.\n\n1. **Open your queue.** Sidebar → **Cases**. Tightest deadlines sort to the top — the response window waits for no one. *(See \"Your worklist\".)*\n2. **Open a hit.** You get the **decision cockpit**: applicant, order and package, the check and hit type, record age, identity-match facts, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any attached records, applies the FCRA/EEOC grounding, and drafts a recommended disposition with cited reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Ground it in the record.** Attach the source documents (court record, verification result) so the recommendation rests on the real evidence the pre-adverse packet will need. *(See \"Evidence\".)*\n5. **Record your disposition.** *Clear — no reportable adverse information* when the hit resolves benign; *Report adverse finding* only when verified, reportable, and job-related; *Suppress — not reportable* for an obsolete or mismatched record; *Request identity verification* when the match is below the accuracy bar; or *Route to individualized assessment* on a borderline conviction. A note is required on every code.\n6. **Hand off.** Your disposition becomes a **proposal** the Operations Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Identity Resolution Specialist",
      "tagline": "Dig into common-name and mixed-file hits — confirm the match meets the accuracy bar.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "entity-resolution",
        "datasets"
      ],
      "steps": "You take the hits where identity is in doubt — the common-name felony match, the mixed file — where reporting the wrong person is the classic FCRA failure.\n\n1. **Pick up escalations.** Filter **Cases** to the *Request identity verification* status or your assignments. *(See \"Your worklist\".)*\n2. **Work the cockpit.** Line up middle name, full DOB, and address history against the source record; the Copilot summarizes the applicant's prior orders and any dispute history and cites the specifics. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Resolve the identity.** Use **entity resolution** to see whether the hit's source record actually belongs to this applicant or is a mixed file across similar names. *(See \"Entity resolution\".)*\n4. **Query the data.** You can author and **export** queries over the screening datasets to corroborate — pull the record set behind a match and confirm the 607(b) maximum-possible-accuracy standard is met before anything moves toward a report. *(See \"Datasets\".)*\n5. **Decide.** Propose *Suppress — not reportable* on a confirmed mismatch, or send it forward once the identity is corroborated. Notes are your audit trail — say exactly which corroborating facts closed the gap."
    },
    {
      "roleName": "Adverse Action Coordinator",
      "tagline": "Run the pre-adverse / adverse-action two-step and watch the notice rails.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "notifications"
      ],
      "steps": "Once a finding is reportable, you drive the two-step clock: the pre-adverse notice, the applicant's response window, then the final adverse-action notice — nothing skips a step.\n\n1. **Work the clock queue.** Filter **Cases** to hits dispositioned *Report adverse finding* and sort by deadline runway. *(See \"Your worklist\".)*\n2. **Confirm the packet is grounded.** On the cockpit, verify the verification evidence and the record copy the pre-adverse packet will rest on are present before the clock advances. *(See \"Working a case\".)*\n3. **Watch the rails.** The **Adverse Action Clocks** dashboard shows where each case sits in the two-step and the deadline buckets — never let the customary response window be shortened for convenience. *(See \"Dashboards\".)*\n4. **Check incoming order status.** You have read access to the connections and ingestion feeds, so you can confirm order intake and returned results are current before acting. *(See \"Datasets\".)*\n5. **Stay ahead of breaches.** The **bell** flags approaching deadlines; escalate to the Operations Manager rather than let a window lapse. Sending the actual notice is a governed write — it never leaves on the agent's word. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Screening Operations Manager",
      "tagline": "Own the desk — approve dispositions, watch the clocks, balance the load.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit"
      ],
      "steps": "You run the desk, and you're the one who holds **approve** — reportable adverse findings and final determinations become real only when you say so.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence. **Approve** to write it back or **Reject** with a note. You **can't** approve a proposal you authored — that's the four-eyes rule on every adverse finding. *(See \"Approvals & four-eyes\".)*\n2. **Watch accuracy and clocks.** The **Accuracy & Identity Watch** and **Adverse Action Clocks** dashboards show suppression (accuracy-save) rate, identity-escalation share, and deadline runway — reassign from the **worklist** before anything breaches. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Balance the load.** Use the worklist filters and the **Screening Adjudication Center** dashboard to spot backlog aging across adjudication, identity resolution, and adverse-action coordination, then reassign. *(See \"Working a case\".)*\n4. **Own model promotion.** You also hold model-promotion approval, so when a retrained hit-outcome model is proposed for promotion, you're the governed sign-off. *(See \"Approvals & four-eyes\".)*"
    },
    {
      "roleName": "FCRA Compliance Auditor",
      "tagline": "Read-only oversight — verify every determination was made, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for exam readiness. Your job is to confirm the desk followed FCRA accuracy, obsolescence, and adverse-action duties — and that every determination is evidenced and four-eyed.\n\n1. **Review resolved cases.** Open **Cases** and inspect closed hits: the disposition, the required **note**, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the record trail.** Confirm the verification evidence and source records are attached and were cited — especially on suppressions and identity escalations. *(See \"Working a case\".)*\n3. **Monitor at scale.** Use the **Accuracy & Identity Watch** and **Adverse Action Clocks** dashboards to watch suppression rate, identity-escalation share, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Inspect the models.** You can read the training **pipelines**, runs, and eval trends behind the order-anomaly and hit-outcome scorers to confirm the scoring capability is governed and reproducible. *(See \"Pipelines\".)*\n5. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

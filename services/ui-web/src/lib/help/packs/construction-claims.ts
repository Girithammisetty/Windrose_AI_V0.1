import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/construction-claims/). */
export const constructionClaimsGuide: PackGuide = {
  "packName": "construction-claims",
  "displayName": "Construction Claims",
  "summary": "\nAI-assisted **construction claim and change-order adjudication** for the paying side of the contract — owners and developers, general contractors, construction managers, sureties, and infrastructure agencies. It runs claim intake triage with **contractual notice-provision awareness**, entitlement-first analysis (differing site conditions Type I/II, directed vs constructive acceleration, concurrent delay), and quantum review grounded in measured-mile vs total-cost methodology, plus defect-backcharge, surety-notice, and pay-if-paid/pay-when-paid payment-dispute screening.\n\nOn top of the workflow it ships a claims-operations KPI **semantic model and dashboards** (approval/rejection/negotiated rates, claimed-vs-approved ratio, deadline runway, schedule-impact mix), a party-to-project exposure network, claims-law grounding memories, and contract-anomaly + claim-outcome training pipelines — everything the desk needs to adjudicate defensibly.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded construction-claims worklist (claims queue) with contractual deadline runway",
        "Five dispositions: approve change order, reject (no entitlement), negotiate (partial merit), request substantiation, close (withdrawn or resolved) — each requires a note citing the governing clause and record evidence"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"construction_claims_core\" semantic model (approval/rejection/negotiated rates, approved-to-claimed ratio, deadline runway, delay-claim share, backlog aging)",
        "Three dashboards: Claims Command Center, Entitlement & Schedule Impact, Party Risk & Recovery",
        "A party-to-project exposure network plus verified and saved canonical questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A claim-intake triage copilot and a claims-operations analytics agent (proposal-mode, four-eyes)",
        "Claims-law grounding memories (contract law, DSC clause, mechanics lien, surety bonds, prompt payment)",
        "Contract-anomaly (isolation forest) and claim-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Claims Analyst",
      "tagline": "First touch — triage new claims and change orders, entitlement first.",
      "usesCapabilities": [
        "getting-started",
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence"
      ],
      "steps": "You are the front line: new construction claims and change-order requests land in your queue, and contractual notice and response deadlines are already ticking.\n\n1. **Get oriented.** If this is your first time, the welcome flow shows you where cases, dashboards, and the copilot live. *(See \"Getting started\".)*\n2. **Open your queue.** Sidebar → **Cases**. Claims closest to a contractual response deadline sort to the top — analysis depth should never blow a notice clock. *(See \"Your worklist\".)*\n3. **Open a claim.** You get the **decision cockpit**: project, contract, claiming party, claim type, claimed amount, notice timeliness, and the deadline runway. *(See \"Working a case\".)*\n4. **Run the triage Copilot.** It reads the case and any attached records, applies the claims-law grounding, and drafts a recommended disposition — entitlement first, quantum second — as a **proposal** citing the governing clause. *(See \"The Copilot\".)*\n5. **Attach evidence.** Pull in the notice letters, daily reports, RFIs, or schedules so the recommendation is grounded in the real record, not assertion. *(See \"Evidence\".)*\n6. **Record your disposition.** Propose *Approve — change order*, *Reject — no contractual entitlement*, *Negotiate — partial merit*, *Request substantiation* when causation or quantum is unsupported, or *Close — withdrawn or resolved*. A note is required — capture the clause and the records the determination letter will rely on.\n7. **Hand off.** Your disposition becomes a **proposal** the Claims Review Board Manager approves — you can't approve your own."
    },
    {
      "roleName": "Scheduling & Delay Specialist",
      "tagline": "Run the CPM/TIA analysis behind delay, acceleration, and quantum claims.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "semantic-models",
        "datasets",
        "copilot"
      ],
      "steps": "You are the schedule and quantum expert: when a claim turns on critical-path proof or measured-mile substantiation, it comes to you.\n\n1. **Pick up assigned claims.** Filter **Cases** to delay, acceleration, and productivity claims assigned to you. *(See \"Your worklist\".)*\n2. **Work the cockpit.** Review the as-planned vs as-built story, windows, and concurrent-delay exposure on the case, with the attached schedules and daily reports. *(See \"Working a case\".)*\n3. **Author the analysis.** Query the governed **datasets** and the `construction_claims_core` **semantic model** to test the claimed critical-path impact and benchmark productivity against a measured mile — then export the result to back your determination. *(See \"Semantic models\" and \"Datasets\".)*\n4. **Let the Copilot draft the narrative.** It frames the time-impact reasoning and flags where total-cost pricing is standing in for real substantiation. *(See \"The Copilot\".)*\n5. **Record your disposition.** Propose *Negotiate — partial merit* anchored to the demonstrated production rate, *Request substantiation* for the exact records still missing, or support *Reject* where there is no compensable, non-concurrent delay. Notes are required.\n6. **Reassign** back to intake or over to contract administration when the claim's center of gravity shifts."
    },
    {
      "roleName": "Contract Administrator",
      "tagline": "Own the contract record and the incoming document feeds every claim is judged against.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "datasets",
        "notifications"
      ],
      "steps": "You are the custodian of the record: the contract terms, the notice history, and the document feeds (RFIs, submittals, daily reports, correspondence) that every entitlement decision is measured against.\n\n1. **Keep the record current.** Incoming project-controls and document feeds land as governed **datasets** in their landing shape; production connectors are configured through **Data → Connections**. *(See \"Datasets\".)*\n2. **Attach the contract evidence.** On each claim's cockpit, make sure the governing contract clauses, the notice-and-cure trail, and the supporting documents are attached so analysts and the copilot reason over the real record. *(See \"Working a case\" and \"Evidence\".)*\n3. **Screen notice hygiene.** From your queue, flag claims where timely written notice — often a condition precedent — is late or missing, so the entitlement question is framed correctly from the start. *(See \"Your worklist\".)*\n4. **Support determinations.** Propose *Request substantiation* when the record is incomplete, or add the contract citations behind a *Reject — no entitlement* recommendation. Notes are required.\n5. **Stay in the loop.** Watch the **bell** for new document feeds, assignments, and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Claims Review Board Manager",
      "tagline": "Own the desk — approve every determination, watch exposure, govern the models.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the review board. You are the one who holds **approve**, so no determination, rejection, or negotiated settlement becomes real until you sign it.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored — that's the four-eyes control. *(See \"Approvals & four-eyes\".)*\n2. **Watch exposure and runway.** Open the **Claims Command Center** and **Entitlement & Schedule Impact** dashboards for approval/rejection/negotiated rates, approved-to-claimed ratio, deadline runway, and schedule-impact mix; **click** a segment to cross-filter. *(See \"Dashboards\".)*\n3. **Manage party risk.** The **Party Risk & Recovery** dashboard and its party-to-project exposure network surface repeat claimants and surety-backed parties worth escalating. *(See \"Dashboards\".)*\n4. **Balance the load.** Reassign from the **worklist** so nothing breaches a contractual response deadline. *(See \"Your worklist\" and \"Working a case\".)*\n5. **Govern the scoring models.** The pack's contract-anomaly and claim-outcome **pipelines** feed model versions; you review and **approve model promotions** before anything informs the desk. *(See \"Pipelines\".)*"
    },
    {
      "roleName": "Project Controls Auditor",
      "tagline": "Read-only oversight — verify every determination was made, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "datasets",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for audit and surety/lender reporting readiness. Your job is to confirm the desk followed the contract and the record — and that every determination is evidenced and four-eyed.\n\n1. **Review resolved claims.** Open **Cases** and inspect closed claims: the disposition, the required **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Your worklist\" and \"Working a case\".)*\n2. **Check the evidence trail.** Confirm each determination cites the governing clause and the record evidence it relied on — the defensibility standard for a determination letter. *(See \"Working a case\".)*\n3. **Verify lineage.** Trace the governed **datasets** feeding the analysis and review the model **pipelines** and their runs so the scoring is explainable and reproducible. *(See \"Datasets\" and \"Pipelines\".)*\n4. **Monitor at scale.** Use the dashboards to watch approval/rejection rates, quantum discipline, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

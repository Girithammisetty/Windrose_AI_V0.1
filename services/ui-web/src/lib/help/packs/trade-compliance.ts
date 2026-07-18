import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/trade-compliance/). */
export const tradeComplianceGuide: PackGuide = {
  "packName": "trade-compliance",
  "displayName": "Trade Compliance",
  "summary": "AI-assisted **customs and trade-compliance decisioning** for US importers, customs brokers, and freight forwarders. It runs the review desk end to end: **HS classification review** with GRI-ordered reasoning and reasonable-care awareness, **denied-party screening** adjudication (OFAC SDN, BIS Entity List, the 50% rule) cleared on evidence, **export/dual-use license-determination** escalation, and origin/transshipment verification — each recommendation drafted by an AI copilot and written back only after a second person approves (four-eyes).\n\nEvery decision lives against the regulatory clock and stays proposal-mode: holds, corrected entries, and releases route exclusively to human analysts, with the Trade Compliance Manager as the sole approver. The pack layers the domain semantic model, dashboards, dispositions, agents, and training pipelines on top of the shared Windrose platform surfaces.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded trade-compliance review queue with deadline runway",
        "Five dispositions: confirm declared classification/origin, reclassify & file corrected entry, sanctions true hit (hold, block & report), escalate to export/dual-use licensing review, release (screening match cleared as false positive)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"trade_core\" semantic model (classification correction rate, screening false-positive rate, true hits, duty-risk mix, deadline runway, entered value by lane/mode)",
        "Three dashboards: Trade Compliance Command Center, Screening & Sanctions, Classification & Duty Risk",
        "Verified & saved canonical trade questions plus importer-lane sourcing network analytics"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A trade-review triage copilot and a trade-analytics agent (specialized platform agents, proposal-mode)",
        "Customs (HTSUS/CBP) and sanctions (OFAC SDN / BIS Entity List / EAR) grounding memories",
        "Shipment-anomaly (isolation forest) and review-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Classification Analyst",
      "tagline": "First touch — review declared HS classification and origin, and start the review clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: entries land in your queue for classification and origin review, and the reasonable-care clock is already running.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadlines sort to the top — the regulatory clock waits for no one. *(See \"Your worklist\".)*\n2. **Open an entry.** You get the **decision cockpit**: the shipment, entry line, declared HS code, origin claim, entered value, and the deadline. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case, applies the HTSUS/CBP grounding with GRI-ordered heading analysis, and drafts a recommended disposition with reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence** — commercial invoices, spec sheets, rulings — so the recommendation is grounded in the real documents the copilot can cite. *(See \"Evidence\".)*\n5. **Record your disposition.** Propose *Confirm — declared classification/origin verified correct* when it holds, or *Reclassify — declared code wrong, file corrected entry* when it doesn't, carrying the corrected code and reasonable-care / prior-disclosure note. Notes are required.\n6. **Hand off.** Your disposition becomes a **proposal** the Trade Compliance Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Screening Analyst",
      "tagline": "Adjudicate denied-party alerts — clear a false positive or confirm a true hit.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You own denied-party screening: OFAC SDN, BIS Entity List, and 50%-rule alerts land with you, and each one blocks or releases a shipment.\n\n1. **Pick up screening alerts.** Filter **Cases** to your assignments and screening-adjudication items. *(See \"Your worklist\".)*\n2. **Adjudicate on the cockpit.** Compare the trading partner against the matched watch-list entry, weigh the distinguishing data, and review the **evidence** attached to the case. The Copilot summarizes the match and grounds it in the sanctions memories. *(See \"Working a case\", \"Evidence\" and \"The Copilot\".)*\n3. **Look for patterns.** Open the **Screening & Sanctions** dashboard and **click** a list or partner segment to cross-filter the rest — a fast way to see if a partner is a repeat match or an obvious false positive. *(See \"Dashboards\".)*\n4. **Decide.** Propose *Release — screening match cleared as false positive* on documented distinguishing evidence, or *True hit — hold shipment, block and report* when the match stands. A hold can only become real once the Manager approves.\n5. **Reassign** to licensing if the block turns on an export-control question rather than a sanctions match."
    },
    {
      "roleName": "Licensing Specialist",
      "tagline": "Own export/dual-use license determinations and trace them back to the source feeds.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "copilot",
        "datasets"
      ],
      "steps": "You handle the escalations that turn on export controls — dual-use classification and license determination under the EAR.\n\n1. **Work licensing escalations.** Filter **Cases** to items dispositioned *Escalate to export/dual-use licensing review*. *(See \"Your worklist\".)*\n2. **Build the determination.** On the cockpit, confirm the product's control status and end-use/end-user picture, and assemble the **evidence** a license determination requires; the Copilot grounds the EAR/BIS reasoning. *(See \"Working a case\", \"Evidence\" and \"The Copilot\".)*\n3. **Trace the source feed.** You can read the ingestion connections and datasets behind the entry, so you can confirm where the classification and party data actually came from. *(See \"Datasets\".)*\n4. **Disposition** the determination with the required note — whether the item needs a license, qualifies for an exception, or should return to the queue. Any external filing stays a **proposal** the Manager approves — no license application goes out autonomously."
    },
    {
      "roleName": "Trade Compliance Manager",
      "tagline": "Own the desk — approve every disposition, watch the clocks, promote the models.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "notifications"
      ],
      "steps": "You run the desk. You alone hold **approve**, so holds, corrected entries, and releases become real only when you sign off — that's the four-eyes wall.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **Trade Compliance Command Center** and **Classification & Duty Risk** dashboards show backlog aging, deadline runway, and duty-risk mix — reassign from the **worklist** to keep anything from breaching. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Manage sanctions holds.** A hold is only lifted with your approval and a documented distinguishing rationale — review the evidence on the cockpit before you release. *(See \"Working a case\".)*\n4. **Promote improved models.** When a retrained shipment-anomaly or review-outcome model is proposed for promotion, you review and approve it — the same governed second-sign-off applied to the models that assist the desk.\n5. **Balance the load.** Use worklist filters to spot bottlenecks across classification, screening, and licensing, and reassign. Keep the **bell** in view for escalations. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Trade Audit Lead",
      "tagline": "Read-only oversight — CBP audit / focused-assessment readiness across every decision.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight for CBP audit and focused-assessment readiness. Your job is to confirm the desk exercised reasonable care and that every decision was evidenced and four-eyed — without changing anything.\n\n1. **Review resolved cases.** Open **Cases** and inspect closed reviews: the disposition, the **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the supporting documents — invoices, rulings, screening rationale — are attached and were cited. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use the dashboards to watch classification correction rate, screening false-positive rate, true hits, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the lineage.** You can read dataset profiles, lineage, pipeline runs, and model provenance behind any decision — the full path from source feed to disposition. *(See \"Datasets\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

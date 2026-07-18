import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/trucking-claims/). */
export const truckingClaimsGuide: PackGuide = {
  "packName": "trucking-claims",
  "displayName": "Trucking & Logistics Claims",
  "summary": "AI-assisted **trucking and logistics claims & safety adjudication** for motor carriers, freight brokers, and 3PLs. It covers cargo OS&D claim triage with **Carmack-liability and filing-deadline awareness** (the 9-month claim / 2-year suit windows on standard bill-of-lading terms), reefer temperature-excursion evidence workflow, upstream-carrier recovery filing, double-brokering and carrier identity-theft fraud vetting, and telematics safety-event review with litigation-discoverable coaching discipline.\n\nIt ships the semantic model, dashboards, dispositions, AI agents, grounding memories, and training pipelines to run the whole claims-and-safety desk — every claim payment, denial, recovery filing, and preventability determination stays **proposal-mode with a second-person approval** (four-eyes).",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded claims-and-safety worklist (queue) with filing-deadline runway",
        "Five dispositions: accept & pay claim, deny/clear with documented findings, file recovery against the responsible carrier, escalate to fraud investigation (double-broker / staged-loss), close safety event (coaching completed & documented)",
        "Every disposition requires a documented note that survives litigation discovery"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"trucking_claims_core\" semantic model (pay rate, denial rate, recovery rate, deadline runway, coaching closure, carrier risk mix, on-time share, claim backlog aging)",
        "Three dashboards: Claims & Safety Command Center, Cargo Claims & Recovery, Carrier Risk Watch",
        "Carrier-lane network analytics and verified/saved canonical questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A trucking-specialized claims/safety triage copilot and a claims-and-safety analytics agent",
        "Carmack Amendment, 49 CFR Part 370, FMCSA/CSA, and bill-of-lading grounding memories",
        "Shipment-anomaly (isolation forest) and claim-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Claims Analyst",
      "tagline": "First touch — triage cargo OS&D claims and safety events, propose the disposition.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: new cargo claims, reefer excursions, and safety events land in your queue, and the bill-of-lading clocks (9 months to file, 2 years to sue after a declination) are running the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. Items with the tightest **days-to-deadline** sort to the top — a filing or payment-release decision at risk waits for no one. *(See \"Your worklist\".)*\n2. **Open a claim.** You get the **decision cockpit**: claimant, shipment, carrier, lane, amount, seal and temperature records, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and the attached **evidence**, applies the Carmack burden-shifting and bill-of-lading grounding, and drafts a recommended disposition with row-level reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence** — POD exceptions, seal records, reefer downloads, photos, scale tickets — so the recommendation is grounded in the real documents, not a guess. *(See \"Evidence\".)*\n5. **Record your disposition.** Propose *Accept — pay claim* when liability is confirmed, *Deny — documented findings* (the note must carry the findings the claimant can be told and the bill-of-lading defense relied upon), *File recovery against responsible carrier*, or *Escalate to fraud investigation*. Add the required note.\n6. **Hand off.** Your disposition becomes a **proposal** the Claims & Safety Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Carrier Compliance Analyst",
      "tagline": "Own carrier vetting and fraud analytics — screen double-brokering and identity theft.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "datasets",
        "dashboards",
        "copilot"
      ],
      "steps": "You run the vetting and fraud-screening side: is this carrier who it says it is, and does this load carry the double-broker or staged-loss signature?\n\n1. **Work fraud-vetting items.** Filter **Cases** to your assignments — new-authority carriers, mid-load banking/factoring changes, unreachable references, full-load theft on hot lanes. *(See \"Your worklist\".)*\n2. **Investigate on the cockpit.** Review the carrier's safety tier, authority age, insurance status, and prior-claims history against the load. The Copilot cites the specific carrier ids, lanes, and fraud signatures. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Query the data.** You have query authoring and **export** — pull the carrier and shipment **datasets**, profile them, and follow lineage to see where a carrier's identity or banking details drifted. *(See \"Datasets\".)*\n4. **Watch the risk mix.** Open the **Carrier Risk Watch** dashboard and **click** a segment (watch-tier carriers, lapsed insurance, high fraud-risk) to cross-filter the rest — a fast way to spot a repeat offender across loads. *(See \"Dashboards\".)*\n5. **Decide.** Propose *Escalate to fraud investigation* when the double-broker or identity-theft pattern holds and payment should freeze pending verification, or *Deny / clear the flag — documented findings* when the carrier checks out. Notes are required and are your audit trail; the Claims & Safety Manager approves."
    },
    {
      "roleName": "Safety Review Specialist",
      "tagline": "Own telematics safety-event review — evidence-first, coaching documented for discovery.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "notifications"
      ],
      "steps": "You review harsh-event, forward-camera, and ELD safety events. Because safety-review files are discoverable in litigation, every preventability view has to be evidence-first and consistently documented.\n\n1. **Pick up safety events.** Filter **Cases** to telematics safety-event items in your queue. You also have read visibility into the ingestion feeds behind them. *(See \"Your worklist\".)*\n2. **Review the actual evidence.** On the cockpit, open the video/telematics **evidence** attached to the event before forming any preventability view — a disputed event from a long-tenured driver gets the same evidence-first review as any other. *(See \"Working a case\" and \"Evidence\".)*\n3. **Use the Copilot** to summarize the event context and prior coaching, grounded in the FMCSA/CSA memories — as a proposal, never a verdict. *(See \"The Copilot\".)*\n4. **Close the loop.** Propose *Close safety event — coaching completed & documented* once the review is done and the coaching record is captured, or *Escalate* if the event needs deeper investigation. The note must carry the documented coaching that will survive discovery.\n5. **Hand off.** Your disposition is a **proposal** the Claims & Safety Manager approves. Keep the **bell** in view for new events and reassignments. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Claims & Safety Manager",
      "tagline": "Own the desk — hold approve, promote scored models, watch the deadline runway.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the desk. You alone hold **approve** on dispositions, so claim payments, denials, recovery filings, and preventability determinations become real only when you say so — that's the four-eyes.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks and the mix.** The **Claims & Safety Command Center** shows pay rate, denial rate, recovery rate, coaching closure, and **deadline runway**; reassign from the **worklist** to keep anything from breaching. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Track recovery and risk.** Use **Cargo Claims & Recovery** and **Carrier Risk Watch** to see where recovery is winning and which carriers need action. *(See \"Dashboards\".)*\n4. **Promote the scoring models.** The pack ships shipment-anomaly and claim-outcome training **pipelines**; a trained model reaches production only when you approve its promotion — review the run before you do. *(See \"Pipelines\".)*\n5. **Balance the load.** Use worklist filters to spot bottlenecks across claims, carrier vetting, and safety review, and reassign. *(See \"Working a case\".)*"
    },
    {
      "roleName": "Fleet Compliance Auditor",
      "tagline": "Read-only oversight — verify every determination was evidenced, four-eyed, and discovery-ready.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk followed Carmack claims practice and FMCSA safety discipline — and that every determination is evidenced, documented, and four-eyed for litigation discovery.\n\n1. **Review resolved cases.** Open **Cases** and inspect closed claims and safety events: the disposition, the **note** (findings and the bill-of-lading defense, or the documented coaching), who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\".)*\n2. **Confirm the evidence trail.** On each case, verify the supporting documents — POD exceptions, seal and temperature records, safety video — are attached and were cited. *(See \"Working a case\".)*\n3. **Monitor at scale.** Use the dashboards to watch pay rate, denial rate, recovery rate, coaching closure, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Audit the models.** Review the training **pipelines**, runs, and model lineage behind the scoring, plus eval trends — read-only. *(See \"Pipelines\".)*\n5. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

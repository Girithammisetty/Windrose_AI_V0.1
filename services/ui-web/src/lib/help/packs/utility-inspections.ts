import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/utility-inspections/). */
export const utilityInspectionsGuide: PackGuide = {
  "packName": "utility-inspections",
  "displayName": "Utility Asset Inspections",
  "summary": "\nAI-assisted **inspection-finding triage and repair/defer decisioning** for electric and gas utilities — wildfire-exposed distribution utilities, transmission operators, and inspection-services firms. It intakes findings from drone-AI, LiDAR, infrared, and foot-patrol sources with **wildfire risk-zone awareness** (HFTD tiers, PSPS-history circuits), applies gas leak-grading practice, and manages false detections through a **field-verification detector-training loop**.\n\nEvery confirmed-hazard dispatch, governed deferral, or false-detection closure is a human determination approved by a second person (four-eyes). The pack ships the seeded finding queue, inspection-program KPI analytics, AI agents, and the anomaly/disposition training pipelines to run the whole triage desk.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded inspection-finding worklist (queue) with make-safe deadline runway",
        "Five dispositions: dispatch immediate repair (confirmed hazard), close — false detection, schedule planned work (governed deferral), request field verification, close — monitored/stable",
        "An engineering-rationale note required on every disposition, so deferrals stay defensible and discoverable"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "An \"inspections_core\" semantic model (immediate-dispatch rate, false-detection rate, verification share, Tier-3 finding share, backlog aging, deadline runway, repair-cost exposure)",
        "Three dashboards: Inspection Triage Center, Wildfire Risk Zones, Detection Quality & Backlog",
        "Verified and saved queries plus asset/circuit registry analytics"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A finding-triage copilot and an inspection-program analytics agent (proposal-mode, four-eyes)",
        "Wildfire-mitigation, gas leak-grading, and NERC grounding memories",
        "Two training pipelines: asset-condition anomaly detector (isolation forest) and finding-disposition scorer (xgboost)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Inspection Triage Analyst",
      "tagline": "First touch — review detections and propose a repair/defer disposition.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: inspection findings from drone-AI, LiDAR, infrared, and foot patrols land in your queue, and make-safe deadlines on immediate-priority findings start running the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadline runway and highest-risk zones sort to the top — a Tier-3 high fire-threat finding on a PSPS-history circuit outranks an equal finding in a standard zone. *(See \"Your worklist\".)*\n2. **Open a finding.** You get the **decision cockpit**: asset, circuit, district, detection source and confidence, risk zone, severity, deadline, and the asset's history (prior findings, install decade, material, criticality). *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the finding and its **evidence**, applies the wildfire-mitigation and leak-grading grounding, and drafts a recommended disposition with cited reasoning — as a **proposal**. *(See \"The Copilot\" and \"Evidence\".)*\n4. **Record your disposition.** Confirmed hazard → *Dispatch immediate repair*; a detection artifact → *Close — false detection* (your note becomes a detector-retraining label); a real defect with no immediate hazard → *Schedule planned work* with a documented engineering rationale and program window; low-confidence or conflicting reads → *Request field verification*. Every disposition **requires a note**.\n5. **Never re-monitor a worsening trend.** A repeat finding on an asset that already had a monitor decision must escalate, not quietly go back to *Close — monitored, stable*.\n6. **Hand off.** Your disposition becomes a **proposal** the Asset Risk Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Field Verification Engineer",
      "tagline": "Confirm or refute detections in the field — and feed the detector-training loop.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You take the inconclusive detections — the ones the desk can't resolve from the imagery alone — and settle them on the ground.\n\n1. **Pick up verification work.** Filter **Cases** to your assignments and the *Request field verification* status. *(See \"Your worklist\".)*\n2. **Investigate on the cockpit.** Review the detection source, confidence, and any conflicting reads, then attach your field **evidence** — instrument-survey results, close-up imagery, leak-grading readings. The Copilot summarizes the asset history and cites the documents. *(See \"Working a case\", \"Evidence\", and \"The Copilot\".)*\n3. **Screen for artifacts.** Open the **Detection Quality & Backlog** dashboard and **click** a segment (detection source, confidence band) to cross-filter — low-confidence drone-AI reads matching a known shadow/glint signature belong in field verification, not dispatch. *(See \"Dashboards\".)*\n4. **Settle the detection.** Update the disposition: confirmed hazard → *Dispatch immediate repair*; artifact → *Close — false detection*, whose note becomes a **detector-retraining label**; verified stable → *Close — monitored, stable* with a re-check basis. Notes are required and are your audit trail.\n5. **Reassign** back to triage or over to the vegetation program if the finding belongs there."
    },
    {
      "roleName": "Vegetation Program Specialist",
      "tagline": "Own the clearance program — turn vegetation findings into governed planned work.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "datasets",
        "dashboards"
      ],
      "steps": "You own overhead-line clearance. Vegetation-span findings — especially in high fire-threat districts — flow to you for program decisioning.\n\n1. **Work vegetation findings.** Filter **Cases** to your assignments and vegetation finding types. *(See \"Your worklist\".)*\n2. **Assess on the cockpit.** Review the span, circuit, risk zone, and clearance evidence; the Copilot grounds the recommendation in overhead-line clearance standards and wildfire-mitigation commitments. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Check the risk picture.** Open the **Wildfire Risk Zones** dashboard and cross-filter by district and circuit to see where Tier-3 exposure and PSPS history concentrate the program's urgency. *(See \"Dashboards\".)*\n4. **Cross-reference the source data.** When you need the underlying asset or circuit rows behind a finding, browse the seeded **datasets** the pack ships in their landing shape. *(See \"Datasets\".)*\n5. **Disposition.** Immediate exposure → *Dispatch immediate repair*; a real clearance need with no immediate hazard → *Schedule planned work* with the engineering rationale and program window. Add the required note — your disposition becomes a **proposal** the Asset Risk Manager approves."
    },
    {
      "roleName": "Asset Risk Manager",
      "tagline": "Own the desk — approve dispositions, govern deferrals, promote models, watch the clocks.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the triage desk. You alone hold **approve**, so proposals become real — and repair/defer decisions get made — only when you say so. Immediate-dispatch vs deferral is a four-eyes decision.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Scrutinize deferrals.** For every *Schedule planned work* proposal, confirm the engineering rationale and program window are documented — deferred-repair records are discoverable if the asset later fails. Reject silent re-monitors of a worsening trend.\n3. **Watch the clocks.** The **Inspection Triage Center** dashboard shows immediate-dispatch rate and deadline runway; **Wildfire Risk Zones** shows Tier-3 concentration. Reassign from the **worklist** so no make-safe deadline breaches. *(See \"Dashboards\" and \"Your worklist\".)*\n4. **Balance the load.** Use worklist filters and **bulk** actions to spot bottlenecks and rebalance across triage, field verification, and the vegetation program.\n5. **Govern the models.** The pack's anomaly and finding-disposition training **pipelines** produce candidate models; you review and **approve promotion** before any scored model influences the desk. *(See \"Pipelines\".)*"
    },
    {
      "roleName": "Regulatory Compliance Auditor",
      "tagline": "Read-only oversight — verify every determination was made, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for wildfire-mitigation-plan and inspection-cycle audit readiness. Your job is to confirm the desk followed the standards — and that every determination is evidenced and four-eyed. You can see everything and change nothing.\n\n1. **Review resolved findings.** Open **Cases** and inspect closed findings: the disposition, the required **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the detection evidence and field-verification documents are attached and were cited — especially for immediate-dispatch and deferral calls. *(See \"Evidence\".)*\n3. **Test deferral discipline.** Verify every *Schedule planned work* carries a documented engineering rationale and program window, and that no repeat finding was silently re-monitored after a prior monitor decision.\n4. **Monitor at scale.** Use the **Detection Quality & Backlog** and **Inspection Triage Center** dashboards to watch false-detection rate, backlog aging, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Trace the models.** Review the training **pipelines** and their run history to confirm scored models were governed and promotion was approved. *(See \"Pipelines\".)*\n6. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*"
    }
  ]
};

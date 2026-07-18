import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/device-complaints/). */
export const deviceComplaintsGuide: PackGuide = {
  "packName": "device-complaints",
  "displayName": "Medical Device Complaints",
  "summary": "\nAI-assisted **medical-device complaint handling and MDR reportability** for manufacturers, combination-product makers, and contract manufacturers (21 CFR 803 / 820.198, with EU MDR vigilance as the parallel regime). It handles complaint intake triage with **regulatory-clock awareness** — the 30-calendar-day and 5-work-day reporting windows, with the becoming-aware date as day zero — death-reportability presumption handling, malfunction could-recur assessment, and 820.100 CAPA trend escalation.\n\nIt ships the semantic model, dashboards, dispositions, grounding memories, and AI agents to run the whole complaint desk — every reportability decision routed to a human, and any MDR filing or not-reportable rationale held to **four-eyes** approval.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded complaint worklist (queue) with MDR regulatory clocks",
        "Five dispositions: file MDR report, close — not reportable (rationale documented), open CAPA investigation, request device return / info, close — duplicate complaint",
        "A becoming-aware-date deadline runway carried on every case"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A “complaints_core” semantic model (MDR filing rate, not-reportable rate, CAPA-open share, deadline runway, serious-harm and device-problem mix)",
        "Three dashboards: Complaint Handling Center, MDR Clock & Reportability, Device-Problem Signals",
        "Verified and saved canonical questions over the governed model"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A complaint-intake triage copilot and a device-quality analytics agent (both proposal-mode)",
        "Part 803/820 + EU MDR vigilance grounding memories",
        "Device-fleet anomaly (isolation forest) and reportability-scoring (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Complaint Intake Coordinator",
      "tagline": "First touch — log and triage new complaints and start the MDR clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: new device complaints land in your queue, and the MDR reporting clock starts the moment any employee becomes aware — that becoming-aware date is day zero.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadline runway sorts to the top — the 30-calendar-day and 5-work-day windows wait for no one. *(See \"Your worklist\".)*\n2. **Open a complaint.** You get the **decision cockpit**: device, product, lot / software version, device-problem code, patient harm, complaint source, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any **evidence**, applies the Part 803/820 grounding, and drafts a recommended disposition with reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence** — the complaint intake form, service notes, or returned-device photos — so the recommendation is grounded in the real documents. *(See \"Evidence\".)*\n5. **Record your disposition.** For a confirmed reportable event, propose *File MDR report*; for a clear double-report, *Close — duplicate complaint*; if analysis needs the device back, *Request device return / information*. Every disposition requires a note.\n6. **Hand off.** Your disposition becomes a **proposal** the Quality & Regulatory Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Complaint Investigator",
      "tagline": "Run the technical investigation — device analysis, sibling complaints, trend signals.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You take the complaints that need real investigation — device analysis, root cause, and whether this is a one-off or the start of a trend.\n\n1. **Pick up your work.** Filter **Cases** to your assignments. *(See \"Your worklist\".)*\n2. **Investigate on the cockpit.** Review the device history, lot and software-version bands, and the **evidence** attached to the case. Use the Copilot to summarize sibling complaints on the same product and cite the documents. *(See \"Working a case\" and \"Evidence\".)*\n3. **Look for clusters.** Open the **Device-Problem Signals** dashboard and **click** a segment — a device-problem code, a product, a software version — to cross-filter the rest. A third same-problem complaint on one product, or a spike right after a version rollout, is a trend signal. *(See \"Dashboards\".)*\n4. **Decide.** When the signal is systemic, propose *Open CAPA investigation* (the 820.100 trend escalation); if the device or facts are missing, *Request device return / information*. Notes are required and matter — they're your audit trail.\n5. **Reassign** to the MDR Reportability Analyst when the file is ready for the 21 CFR 803 determination."
    },
    {
      "roleName": "MDR Reportability Analyst",
      "tagline": "Own the 21 CFR 803 decision file — the reportability determination.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "copilot",
        "datasets",
        "worklist"
      ],
      "steps": "You own the reportability decision file. Deaths carry a reportability presumption unless the file rules device involvement out, and a malfunction is reportable when a recurrence would likely cause or contribute to death or serious injury.\n\n1. **Work reportability reviews.** Filter **Cases** to files handed off for the 803 determination. *(See \"Your worklist\".)*\n2. **Ground the determination.** On the cockpit, review the **evidence** and pull source-system reads — you have ingestion read access to trace the complaint back to its origin **dataset**. *(See \"Working a case\", \"Evidence\", and \"Datasets\".)*\n3. **Use the Copilot as decision support.** It cites the reporting prong and the specific evidence, and it never predicts the outcome — you make the call. *(See \"The Copilot\".)*\n4. **Record the determination.** Propose *File MDR report* with the reportability rationale the eMDR narrative will cite, or *Close — not reportable* with the documented rationale 820.198 requires. Never let investigation completeness blow a deadline — an MDR can be filed on available information and supplemented later.\n5. **Hand off for approval.** Your determination is a **proposal**; the Quality & Regulatory Manager approves it. You can't file or approve your own."
    },
    {
      "roleName": "Quality & Regulatory Manager",
      "tagline": "Own the desk — approve every disposition, watch the clocks, govern model promotions.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the complaint desk. You alone hold **approve**, so MDR filings and not-reportable rationales become real only when you say so — that's the four-eyes control.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **MDR Clock & Reportability** dashboard shows deadline runway and filing rate; reassign from the **worklist** to keep anything from breaching the 30-day or 5-work-day window. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Run the program view.** Use the **Complaint Handling Center** dashboard for backlog aging, not-reportable rate, CAPA-open share, and serious-harm mix — the figures that feed management review and inspection prep.\n4. **Govern the models.** The pack's anomaly and reportability-scoring **pipelines** train on human dispositions; trained models require your approval before promotion — nothing goes live without a human sign-off. *(See \"Pipelines\".)*\n5. **Balance the load.** Use worklist filters to spot bottlenecks and reassign across intake, investigation, and the reportability desk."
    },
    {
      "roleName": "Quality Systems Auditor",
      "tagline": "Read-only inspection readiness — verify every decision was made and evidenced correctly.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "dashboards",
        "pipelines",
        "datasets"
      ],
      "steps": "You have **read-only** oversight for inspection readiness. Your job is to confirm the desk followed 21 CFR 803/820 and that every decision is evidenced and four-eyed — with no case-write power of your own.\n\n1. **Review closed complaints.** Open **Cases** and inspect the disposition, the **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each file, confirm the supporting documents are attached and were cited, and that not-reportable rationales are documented as 820.198 requires. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use the dashboards to watch MDR filing rate, not-reportable rate, deadline compliance, and CAPA-open share for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the models and data.** Review the training **pipelines**, their runs, and the source **datasets** and lineage behind the governed figures — the provenance an FDA inspection expects. *(See \"Pipelines\" and \"Datasets\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

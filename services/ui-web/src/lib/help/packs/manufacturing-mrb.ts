import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/manufacturing-mrb/). */
export const manufacturingMrbGuide: PackGuide = {
  "packName": "manufacturing-mrb",
  "displayName": "Manufacturing MRB",
  "summary": "\nAI-assisted **nonconformance disposition and Material Review Board (MRB)** workflow for regulated discrete manufacturers — aerospace & defense under **AS9100**, medical-device makers under **ISO 13485 / 21 CFR 820.90**, and automotive tiers under **IATF 16949**. It runs NC intake triage with **disposition-deadline awareness**, MRB authority-limit guardrails (use-as-is on a critical characteristic routes to customer/design-authority approval), supplier-quality watch (repeat-SCAR suppliers, suspect certs), containment and suspect-lot traceability, and escape/customer-notification assessment.\n\nOn top of the workflow it ships a quality-operations KPI **semantic model** and dashboards, supplier-part-family analytics, AS9100 / ISO 13485 / IATF 16949 + 8D/CAPA grounding memories, and lot-anomaly plus disposition-outcome training pipelines. Every AI-drafted disposition is **proposal-mode with four-eyes approval** — the engineer proposes, the Quality Manager signs.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded MRB nonconformance worklist (queue) with disposition-deadline clocks and containment status",
        "Five dispositions: rework to spec (re-verification required), release — no defect found, use-as-is engineering disposition / customer concession, quarantine pending analysis, return to supplier — issue SCAR",
        "Every closure requires a note — the MRB minutes, justification, re-verification plan or SCAR reference an examiner reads"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "An \"mrb_core\" semantic model (rework rate, use-as-is share, scrap share, supplier-return share, backlog aging, deadline runway, detection-point and spec-class mix)",
        "Three dashboards: Nonconformance Command Center, MRB Dispositions & Cycle Time, Supplier Quality Watch",
        "Supplier-part-family network analytics plus seeded verified & saved queries"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A domain-tuned NC intake / MRB triage copilot and a quality-operations analytics agent (proposal-mode, four-eyes)",
        "AS9100 / ISO 13485 / IATF 16949 + 8D/CAPA grounding memories",
        "Two training pipelines: an isolation-forest lot-anomaly detector and an xgboost disposition-outcome scorer"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Quality Engineer",
      "tagline": "First touch — write up nonconformances, triage them, and propose a disposition.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of the MRB. New nonconformances land in your queue, and the disposition deadline starts ticking the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadline runway sorts to the top — a nonconformance at risk of breaching its MRB disposition clock can't wait. *(See \"Your worklist\".)*\n2. **Open a nonconformance.** You get the **decision cockpit**: the NC id, lot, part family, supplier, program, spec class, detection point, serialization status, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the record and any attached **evidence**, applies the AS9100 / ISO 13485 / IATF 16949 grounding, watches the clock, and drafts a recommended disposition with cited reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence.** Pull in inspection results, cert-of-conformance scans, or measurement records so the recommendation is grounded in the real documents. *(See \"Evidence\".)*\n5. **Record your disposition.** *Rework to spec* when the defect is real and correctable (name the re-verification plan in the note); *Release — no defect found* when the parts conform and the NC report was in error; *Quarantine pending analysis* to hold containment while root-cause or escape analysis completes. A note is required on every code.\n6. **Know your limits.** A *use-as-is* on a critical characteristic is not within internal MRB authority — propose it, but it routes to the Quality Manager (and customer/design authority). You can't approve your own disposition.\n7. **Watch the bell** for new assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "MRB Engineering Reviewer",
      "tagline": "Carry the engineering evaluation — justify the disposition and build the package.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "semantic-models"
      ],
      "steps": "You own the engineering side of the review: the stress/justification analysis behind a use-as-is, the re-verification criteria behind a rework, and the data that backs it.\n\n1. **Pick up assigned reviews.** Filter **Cases** to the nonconformances routed to you — typically the ones proposing *Use-as-is engineering disposition* or a contested *Rework to spec*. *(See \"Your worklist\".)*\n2. **Evaluate on the cockpit.** Review the characteristic, the drawing/spec context, and the **evidence**; use the Copilot to summarize prior dispositions on the same part family and cite the records. *(See \"Working a case\" and \"Evidence\".)*\n3. **Author the justification query.** You can write and run ad-hoc queries against the **mrb_core** semantic model — pull the history for this part/characteristic/program and **export** the result for the concession or stress-justification package. *(See \"Semantic models\".)*\n4. **Assemble the package.** Attach the analysis as evidence so the closure carries a defensible engineering record.\n5. **Set or refine the disposition.** Update the case with the engineering call and the re-verification evidence closure will require; add the note. On a critical characteristic, flag that customer/design-authority concurrence is a precondition.\n6. **Hand off for approval.** Your disposition becomes a **proposal** the Quality Manager approves — proposer and approver are always different people."
    },
    {
      "roleName": "Supplier Quality Engineer",
      "tagline": "Own the supplier rail — SCARs, incoming feeds, and repeat-offender watch.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "dashboards"
      ],
      "steps": "When a defect is supplier-caused, ownership transfers to their corrective-action system — and you drive it.\n\n1. **Work supplier nonconformances.** Filter **Cases** to NCs where the defect points upstream. *(See \"Your worklist\".)*\n2. **Investigate on the cockpit.** Confirm the supplier, lot, and cert chain; the Copilot flags a broken cert pedigree (never release on one) and pulls the supplier's prior SCARs and scorecard so a repeat offender is obvious. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Check the certs.** Review the certificate-of-conformance and incoming-inspection **evidence** attached to the case — a suspect cert is its own finding. *(See \"Evidence\".)*\n4. **Watch supplier posture at scale.** Open the **Supplier Quality Watch** dashboard and cross-filter by supplier or part family to see repeat-SCAR patterns and supplier-return share — the signal to escalate to source inspection rather than dispositioning each lot alone. *(See \"Dashboards\".)*\n5. **Disposition.** Propose *Return to supplier — issue SCAR*, citing the prior SCARs and scorecard in the required note. The actual SCAR issuance is a governed write-back that a second person approves.\n6. **Hand off.** Your disposition is a **proposal** the Quality Manager signs — you can't approve your own."
    },
    {
      "roleName": "Quality Manager",
      "tagline": "Own the MRB — approve dispositions, sign the use-as-is calls, promote the models.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You chair the MRB. You alone hold **approve**, so a proposed disposition becomes a real closure only when you sign it.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence. **Approve** to write it back, or **Reject** with a note. Every use-as-is and every critical-characteristic closure carries your signature — and you **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the open book.** The **Nonconformance Command Center** and **MRB Dispositions & Cycle Time** dashboards show backlog aging, deadline runway, and disposition-outcome mix — reassign from the **worklist** to keep anything from breaching its clock. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Spot-check the hard cases.** Drop into the **cockpit** on any use-as-is or escape call to confirm the justification and re-verification evidence are attached before you sign. *(See \"Working a case\".)*\n4. **Balance the load.** Use worklist filters to find bottlenecks across intake, engineering review, and the supplier rail, and reassign.\n5. **Govern the models.** The lot-anomaly and disposition-outcome **pipelines** train on real MRB history; a trained model can't go live until you **approve its promotion** — the same second-person control, applied to the ML that assists the desk. *(See \"Pipelines\".)*"
    },
    {
      "roleName": "Quality Systems Auditor",
      "tagline": "Read-only oversight — prove every disposition was made, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards",
        "pipelines"
      ],
      "steps": "You carry the AS9100 / ISO 13485 internal-audit and certification-body readiness role. You can see everything and change nothing — that's the point.\n\n1. **Review closed nonconformances.** Open **Cases** and inspect the disposition, the **note** (the MRB minutes, justification, or SCAR reference), who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the re-verification records, concession justification, and cert chain are attached and were cited — the objective evidence an examiner reads. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use the **Nonconformance Command Center**, **MRB Dispositions & Cycle Time**, and **Supplier Quality Watch** dashboards to watch rework rate, use-as-is share, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Verify model governance.** Inspect the training **pipelines**, their runs, and the promotion history to confirm no model reached production without review. *(See \"Pipelines\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits, promotions — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You have full read across cases, evidence, analytics, and ML — and no case-write power. That separation is the control."
    }
  ]
};

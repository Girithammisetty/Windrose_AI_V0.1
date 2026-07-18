import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/warranty-claims/). */
export const warrantyClaimsGuide: PackGuide = {
  "packName": "warranty-claims",
  "displayName": "OEM Warranty Claims",
  "summary": "\nAI-assisted **OEM warranty-claims adjudication** for manufacturers paying dealer and service-network claims — auto, heavy-equipment/ag, appliance/electronics, and extended-warranty programs. It handles claim intake triage with **payment-decision deadline awareness**, dealer **claim-padding surveillance** with audit escalation, component **failure early-warning** signal detection by build batch, and supplier **cost recovery** (warranty chargebacks) — all grounded in warranty law (Magnuson-Moss tie-in rule, safety-recall interplay).\n\nEvery determination stays **proposal-mode with four-eyes approval**: an AI copilot drafts a disposition, a human decides, and the Warranty Operations Manager alone approves denials, goodwill, and supplier debits — no autonomous claim payment or debit, ever. The pack ships the dashboards, semantic model, dispositions, and AI agents to run the whole warranty desk.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded warranty-claim worklist (queue) with payment-decision deadline runway",
        "Five dispositions: approve — pay claim in full, deny — policy exclusion/evidence gap, adjust — partial payment, escalate to dealer audit, close — cost recovered from supplier",
        "Case fields carried via display projection (claim, unit, dealer, labor-op, usage, failure mode, amount)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A “warranty_core” semantic model (approval/denial rates, adjustment share, cost per claim, audit-escalation share, supplier recovery, deadline runway, backlog aging)",
        "Three dashboards: Warranty Command Center, Dealer Audit Watch, Component Failure Signals",
        "Verified and saved queries plus dealer–component network analytics"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A claim-triage copilot and a warranty-operations analytics agent (tenant-specialized)",
        "Magnuson-Moss, recall-interplay, and reserve-accounting grounding memories",
        "Unit-anomaly (isolation forest) and claim-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Warranty Claims Analyst",
      "tagline": "First touch — triage dealer claims and watch the payment-decision clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: dealer and service-network warranty claims land in your queue, and each one carries a payment-decision deadline set by the dealer agreement.\n\n1. **Open your queue.** Sidebar → **Cases**. Claims with the tightest deadline runway sort to the top — a decision that breaches its runway costs the desk. *(See \"Your worklist\".)*\n2. **Open a claim.** You get the **decision cockpit**: unit, dealer, labor ops, usage/hour band, failure mode, amount, and the days-to-deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the claim and its **evidence**, applies the warranty-law grounding (it will route a claim implicating an open safety recall to the campaign rather than deny it), and drafts a recommended disposition with citations — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence** — diagnostic reports, photos, repair orders — so the recommendation is grounded in the real documents rather than the claim summary alone. *(See \"Evidence\".)*\n5. **Record your disposition.** *Approve — pay claim in full* for a confirmed covered failure; *Deny — policy exclusion or evidence gap* (the note must state findings the dealer can be told); *Adjust — partial payment* to trim unsupported labor ops; *Escalate to dealer audit* on claim-padding signals. A note is required.\n6. **Hand off.** Your disposition becomes a **proposal** the Warranty Operations Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Technical Assessor",
      "tagline": "Own the hard diagnostics — root-cause failures and repeat-repair comebacks.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You take the claims that need a real engineering call — root-cause disputes, aftermarket-part causation questions, and repeat-repair comebacks.\n\n1. **Pick up assignments.** Filter **Cases** to your queue and the claims flagged for technical review; you can assign and route cases as the diagnostics owner. *(See \"Your worklist\".)*\n2. **Assess on the cockpit.** Work through the diagnostic evidence, usage-meter consistency, and the unit's repair history. A comeback (same unit, same failure, third visit) needs a documented root cause before paying again. *(See \"Working a case\" and \"Evidence\".)*\n3. **Use the Copilot for causation.** Ask it to summarize prior repairs and cite the documents — remember that an aftermarket part justifies denial only when the evidence shows it *caused* the failure (the tie-in prohibition). *(See \"The Copilot\".)*\n4. **Spot clusters.** Open the **Component Failure Signals** dashboard and **click** a component or build batch to cross-filter — the same failure mode at low hours across one batch is a field-quality signal worth flagging for engineering containment. *(See \"Dashboards\".)*\n5. **Decide and note.** Propose *Approve*, *Adjust — partial payment*, or send it toward supplier recovery when the failure is attributable to a supplied component. Your note is the diagnostic record."
    },
    {
      "roleName": "Supplier Recovery Specialist",
      "tagline": "Recover the paid claim's cost from the responsible supplier via warranty chargeback.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "When a paid failure is attributable to a supplied component, you drive the **warranty chargeback** to recover the cost from the supplier.\n\n1. **Work the recovery queue.** Filter **Cases** to claims headed for supplier recovery, and review the connected warranty feeds you can read. *(See \"Your worklist\".)*\n2. **Build the attribution.** On the cockpit, confirm the part and lot evidence that ties the failure to the supplier — no supplier debit without attribution evidence. Assemble the supporting **evidence** the chargeback requires. *(See \"Working a case\" and \"Evidence\".)*\n3. **Track recovery.** The **Dealer Audit Watch** and **Warranty Command Center** dashboards show supplier-recovery rate and open exposure; cross-filter by component or supplier to see where recovery is landing. *(See \"Dashboards\".)*\n4. **Record the outcome.** Disposition the claim *Close — cost recovered from supplier* with the attribution note. The actual debit is a governed write that stays proposal-mode and carries the Operations Manager's approval — you propose, they approve."
    },
    {
      "roleName": "Warranty Operations Manager",
      "tagline": "Own the desk — approve dispositions, watch the clocks, balance the load.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "notifications"
      ],
      "steps": "You run the warranty desk. You alone hold **approve**, so proposals — denials, goodwill adjustments, and supplier debits — become real only when you say so.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored — that's the four-eyes rule. *(See \"Approvals & four-eyes\".)*\n2. **Watch the runway.** The **Warranty Command Center** dashboard shows deadline runway, backlog aging, and cost per claim; reassign from the **worklist** to keep any claim from breaching its payment-decision deadline. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Watch the audit and safety signals.** **Dealer Audit Watch** surfaces claim-padding outliers and audit-escalation share, and **Component Failure Signals** flags emerging field-quality clusters worth an engineering review. *(See \"Dashboards\".)*\n4. **Balance the load.** Use worklist filters to spot bottlenecks and reassign across intake, technical assessment, and supplier recovery — and bulk-action where the same call applies to many claims.\n5. **Approve model promotions.** When the claim-outcome model is retrained, you hold the promotion approval that lets a new version reach the desk. *(See \"Approvals & four-eyes\".)*"
    },
    {
      "roleName": "Warranty Audit Lead",
      "tagline": "Read-only oversight — verify every determination was evidenced and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "datasets"
      ],
      "steps": "You have **read-only** oversight for dealer-audit and financial-control readiness. Your job is to confirm the desk adjudicated each claim on its own evidence and that every determination was four-eyed — you can change nothing.\n\n1. **Review resolved claims.** Open **Cases** and inspect closed claims: the disposition, the **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Trace the lineage.** Inspect the underlying datasets, profiles, and lineage behind a determination, and the pipeline and experiment runs that produced any score — an audit trail from claim to model. *(See \"Datasets & lineage\".)*\n3. **Monitor at scale.** Use the **Dealer Audit Watch** and **Warranty Command Center** dashboards to watch denial rate, adjustment share, audit-escalation share, and supplier recovery for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Rely on the audit log.** Every action — proposals, approvals, edits, exports — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

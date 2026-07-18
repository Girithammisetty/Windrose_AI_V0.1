import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/healthcare-provider-rcm/). */
export const healthcareProviderRcmGuide: PackGuide = {
  "packName": "healthcare-provider-rcm",
  "displayName": "Healthcare Provider RCM",
  "summary": "Provider-side **revenue cycle management** for medical groups, hospitals, and RCM outsourcers. It works the money side of the chart: claim denials, below-contract remittances (underpayments), and aging A/R — grounded in CARC/RARC reason codes, payer-contract terms, and the appeal / timely-filing clocks, with HIPAA, CMS billing (LCD/NCD/NCCI), and No Surprises Act context throughout.\n\nThe pack ships the claim / remit / denial / A/R semantic models and the RCM KPI catalog (clean-claim rate, denial rate, days in A/R, net collection rate), a seeded denials-and-underpayment review queue with RCM dispositions, an RCM command center plus denial-analytics and A/R-aging dashboards, denials-specialist and revenue-cycle-analyst copilot grounding, and a denial-prediction training pipeline. Every write stays **proposal-mode** — the platform forbids autonomous billing writes, so a second person always approves.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded denials + underpayment review queue — high-dollar open denials and below-contract remit lines become day-one cases",
        "Six RCM dispositions: appeal submitted, corrected claim resubmitted, write-off approved, escalate to payer relations, underpayment recovered, payment verified correct",
        "Every disposition rides the four-eyes proposal spine — no autonomous billing writes (BR-1)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "Two governed semantic models — rcm_claims and rcm_ar — with the RCM KPI catalog (clean-claim rate, denial rate, first-pass yield, net collection rate, denial-overturn rate, total A/R balance, % A/R over 90)",
        "Three dashboards: RCM Command Center, Denial Analytics, A/R Aging Actions",
        "Verified and saved canonical RCM questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A denials-specialist triage copilot (CARC/RARC classification, deadline-aware) and a revenue-cycle analytics agent",
        "HIPAA / CMS billing and HFMA MAP-key benchmark grounding memories",
        "A denial-prediction (xgboost) training pipeline on the seed claims table"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Biller",
      "tagline": "First touch — triage denials and short-paid remits, start the deadline clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of the denials desk: new claim denials and below-contract remit lines land in your queue, and the appeal / timely-filing clocks are already running.\n\n1. **Open your queue.** Sidebar → **Cases**. High-dollar denials and the tightest appeal deadlines sort to the top. *(See \"Your worklist\".)*\n2. **Open a case.** You get the **decision cockpit**: payer, claim, CARC/RARC reason code, denied amount (or expected-vs-actual paid on a remit), and the deadline runway. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case, classifies the denial — coverage, documentation, medical-necessity, or administrative — and drafts a recommended action as a **proposal**, checking the deadline is still open. *(See \"The Copilot\".)*\n4. **Attach evidence** — the encounter record, prior auth, or 835 remit line — so the recommendation is grounded in the real documents. *(See \"Evidence\".)*\n5. **Record your disposition.** For a clean administrative overturn, propose *Appeal submitted*; for a coding mismatch, *Corrected claim resubmitted*; when a payment checks out, *Payment verified correct*. A note is required on the actionable ones.\n6. **Hand off.** Your disposition becomes a **proposal** the A/R Manager approves — you can't approve your own. Watch the **bell** for assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Medical Coder",
      "tagline": "Own the coding-driven denials — fix the CPT/ICD-10 linkage and resubmit clean.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence"
      ],
      "steps": "You take the denials that turn on coding: diagnosis/procedure mismatches, NCCI edits, documentation gaps.\n\n1. **Filter to coding denials.** In **Cases**, pull the documentation and administrative denials waiting on a coder — CO-11 mismatches, missing-modifier edits. *(See \"Your worklist\".)*\n2. **Review on the cockpit.** Confirm the CARC/RARC codes, the CPT, and the ICD-10 linkage against the attached chart and encounter record. *(See \"Working a case\" and \"Evidence\".)*\n3. **Ask the Copilot** to cite the specific reason code and the coding rule it implicates, so your correction is defensible in a RAC/MAC audit. *(See \"The Copilot\".)*\n4. **Decide.** Propose *Corrected claim resubmitted* once the linkage is right and you're inside the timely-filing window, or send it toward *Appeal submitted* when the original coding was correct and the payer erred. Notes carry the coding rationale.\n5. **Hand off** — your corrected-resubmit proposal is approved by a second person before anything writes back."
    },
    {
      "roleName": "Denials Specialist",
      "tagline": "Drive appeals and underpayment disputes to recovery.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You own the harder denials and the short-paid remits — the ones that need an appeal packet or a contract-rate dispute.\n\n1. **Work your assignments.** Filter **Cases** to appeals-in-progress and the below-contract remit lines (CO-45 short-pays). *(See \"Your worklist\".)*\n2. **Build the case.** On the cockpit, review the denial history and, for underpayments, the expected contracted amount against the actual paid on the 835 line; attach the fee schedule and clinical evidence the payer will demand. *(See \"Working a case\" and \"Evidence\".)*\n3. **Ground the argument.** The Copilot cites the CARC/RARC codes, the LCD/NCD policy for coverage denials, and confirms you're inside the payer-contract dispute window — anything past it is time-barred and won't be proposed. *(See \"The Copilot\".)*\n4. **Spot the patterns.** Open the **Denial Analytics** dashboard and cross-filter by payer or CARC code to see where appeals are overturning and which payers systematically short-pay. *(See \"Dashboards\".)*\n5. **Disposition** the outcome — *Appeal submitted*, *Underpayment recovered from payer*, *Escalate to payer relations*, or *Write-off approved* when no clinical case exists — with the required note. It becomes a proposal for the A/R Manager."
    },
    {
      "roleName": "A/R Manager",
      "tagline": "Run the desk — approve dispositions, balance the load, watch the aging.",
      "usesCapabilities": [
        "approvals",
        "worklist",
        "case-cockpit",
        "dashboards",
        "notifications"
      ],
      "steps": "You run the A/R desk. You're the one who holds **approve**, so proposed billing actions become real only when you say so.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition — appeal, corrected resubmit, write-off, underpayment recovery — who proposed it, and the reasoning/evidence. **Approve** to write it back or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Handle volume.** Use **bulk** actions to approve a clean batch of low-dollar corrected-resubmits at once, keeping scrutiny on the high-dollar appeals. *(See \"Approvals & four-eyes\".)*\n3. **Watch the aging.** The **A/R Aging Actions** dashboard shows balance by aging bucket and % over 90 — reassign from the **worklist** so nothing breaches its appeal deadline. *(See \"Dashboards\" and \"Your worklist\".)*\n4. **Balance the desk.** Assign denials to billers, coders, and denials specialists by payer, dollar value, and deadline runway. *(See \"Working a case\".)*\n5. **Stay ahead.** The **bell** flags approvals waiting and clocks about to expire. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Revenue Integrity Analyst",
      "tagline": "Build the models, dashboards, and denial-prediction pipeline the desk runs on.",
      "usesCapabilities": [
        "datasets",
        "semantic-models",
        "dashboards",
        "pipelines"
      ],
      "steps": "You own the analytical backbone: the semantic models, the KPI dashboards, and the predictive pipeline that tells the desk which claims are likely to deny.\n\n1. **Profile the data.** Browse the seed **datasets** — 837 claims, 835 remit lines, denials, A/R aging — and profile them to understand distributions before you model. *(See \"Datasets\".)*\n2. **Work the semantic models.** Read and extend the governed **rcm_claims** and **rcm_ar** models so every KPI — clean-claim rate, denial rate, first-pass yield, net collection rate, days in A/R — has one trusted definition. *(See \"Semantic models\".)*\n3. **Author dashboards.** Build and refine the **RCM Command Center**, **Denial Analytics**, and **A/R Aging Actions** views on those measures. *(See \"Dashboards\".)*\n4. **Train the denial predictor.** Run the **Denial prediction (xgboost)** pipeline on the claims table — payer, service line, CPT, clean/first-pass flags → denied outcome — and review the run. *(See \"Pipelines\".)*\n5. **Feed the loop.** Publish the measures and predictions the analytics Copilot and the desk rely on, benchmarked against the HFMA MAP-key targets in grounding."
    },
    {
      "roleName": "Revenue Cycle Director",
      "tagline": "Read-only oversight — verify the numbers, the four-eyes trail, and audit posture.",
      "usesCapabilities": [
        "dashboards",
        "worklist",
        "case-cockpit",
        "approvals"
      ],
      "steps": "You have **read-only** oversight of the whole revenue cycle. Your job is to confirm the desk is hitting benchmark and that every billing action was evidenced and four-eyed — you can see everything and change nothing.\n\n1. **Watch the KPIs.** Open the **RCM Command Center** — clean-claim rate, denial rate, net collection, days in A/R — and export for leadership. Compare against the HFMA benchmarks in grounding. *(See \"Dashboards\".)*\n2. **Review decisions.** Open **Cases** and inspect resolved denials: the disposition, the note, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n3. **Scan for outliers.** Use the **Denial Analytics** and **A/R Aging** dashboards to spot payers, categories, or aging buckets drifting off benchmark — the ones worth a closer look. *(See \"Dashboards\".)*\n4. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log — defensible for RAC/MAC audits — and your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> No claim is submitted, appealed, or written off autonomously — the platform forbids it, and this view is how you prove it."
    }
  ]
};

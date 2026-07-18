import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/pharmacy-benefit-mgmt/). */
export const pharmacyBenefitMgmtGuide: PackGuide = {
  "packName": "pharmacy-benefit-mgmt",
  "displayName": "Pharmacy Benefit Management",
  "summary": "AI-assisted **pharmacy benefit management** for PBMs, health plans, and Medicare Part D sponsors. It runs the Rx **prior-authorization** review desk — grounded in formulary tier, step-therapy, and quantity-limit criteria — alongside **DUR** (drug utilization review) safety-alert triage, with a pharmacist recording every determination and a second reviewer approving any write-back (four-eyes).\n\nOn top of the case desk it ships PBM KPI **semantic models** (generic dispensing rate, PA turnaround against the CMS Part D coverage-determination windows, rebate capture, PMPM), three **dashboards**, a domain-grounded **copilot**, and a PA auto-decision training pipeline. By design the copilot only ever *recommends* — an adverse coverage determination is always a licensed human pharmacist's decision, and controlled-substance PAs never auto-approve.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded PA/DUR review queue (cases) with CMS Part D turnaround clocks (72h standard / 24h expedited)",
        "Five pharmacist dispositions: Rx PA approved, Rx PA denied (human decision), PA pended — prescriber info needed, DUR alert overridden, DUR intervention — contact prescriber"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "Two semantic models: pbm_core (generic dispensing rate, plan-paid PMPM, rebate capture, NCPDP reject mix, DUR alerts) and pbm_pa (PA volumes, approval splits, turnaround, controlled-substance mix)",
        "Three dashboards: PBM Trend & Safety, PA Operations, Rebate & Cost",
        "Verified canonical questions and saved queries over the governed models"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A PA/DUR triage copilot (clinical-pharmacist persona) and a PBM analytics agent, specialized via tenant config",
        "Formulary, step-therapy, and DUR-severity grounding memories",
        "An Rx PA auto-decision classifier training pipeline (recommend-only)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "PA Pharmacist",
      "tagline": "Front-line reviewer — triage Rx prior-auth requests and DUR alerts, record the determination.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the clinical reviewer at the point of decision: Rx prior-authorization requests and DUR safety alerts land in your queue, each on a CMS Part D turnaround clock.\n\n1. **Open your queue.** Sidebar → **Cases**. Expedited PAs on the 24-hour window sort ahead of standard 72-hour ones — the tightest clock rises to the top. *(See \"Your worklist\".)*\n2. **Open a case.** You get the **decision cockpit**: member, prescriber, drug, GPI class, formulary tier, PA/step-therapy/quantity-limit flags, and the turnaround clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any **evidence**, applies the formulary and utilization-management grounding, and drafts a recommended determination — citing the GPI class, tier, and any NCPDP reject or DUR conflict code — as a **proposal**. *(See \"The Copilot\".)*\n4. **Check the evidence.** Attach or review the prescriber's clinical documentation so the recommendation is grounded in the real chart. *(See \"Evidence\".)*\n5. **Record your disposition.** Approve a clean PA (*Rx PA approved*); when criteria aren't met, *Rx PA denied* — that adverse determination is always your human decision, never the agent's. Use *PA pended — prescriber info needed* when documentation is short, and for DUR alerts choose *DUR alert overridden* or *DUR intervention — contact prescriber*. Notes are required on all but a clean approve.\n6. **Watch controlled substances.** CII–CV PAs never auto-approve — they always route to you with the EPCS audit trail noted. Keep an eye on the **bell** for assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Formulary Manager",
      "tagline": "Shape formulary strategy — read the PA desk and build the analytics behind coverage policy.",
      "usesCapabilities": [
        "dashboards",
        "semantic-models",
        "case-cockpit",
        "notifications"
      ],
      "steps": "You own formulary and utilization-management strategy. You don't work individual cases to a decision — you read what the desk is doing and turn it into policy analytics.\n\n1. **Watch the PA desk.** Open the **PA Operations** dashboard: PA volume by urgency tier, decision mix, turnaround against the CMS Part D windows, and the copilot recommendation split. Cross-filter by drug or tier to see where step-therapy and PA rules bite. *(See \"Dashboards\".)*\n2. **Read the trend & safety picture.** The **PBM Trend & Safety** dashboard shows plan-paid trend, therapeutic-class mix, brand/generic split, and the DUR alert mix by conflict code — the signal for where formulary changes would move outcomes. *(See \"Dashboards\".)*\n3. **Look into a case.** When a pattern is worth a closer look, open the underlying PA cases on the **cockpit** to read the determination and note. *(See \"Working a case\".)*\n4. **Build your own view.** Query the governed **semantic models** (pbm_core, pbm_pa) and assemble charts and dashboards for a formulary committee — grounded in the same measures the desk runs on, so numbers reconcile. *(See \"Semantic models\" and \"Dashboards\".)*\n5. **Stay current.** The **bell** surfaces what's changed; use it to keep your analytics aligned with the live queue. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Rebate Analyst",
      "tagline": "Follow the money — rebate capture, drug spend, and PMPM across the governed data.",
      "usesCapabilities": [
        "dashboards",
        "datasets",
        "semantic-models",
        "notifications"
      ],
      "steps": "You track the economics of the benefit: manufacturer rebates, plan-paid spend, and cost share. You work over the governed data, not the case desk.\n\n1. **Start at Rebate & Cost.** Open the **Rebate & Cost** dashboard: expected vs collected rebates by therapeutic class, the monthly collection trend, member copay by formulary tier, and plan-paid mix by channel. *(See \"Dashboards\".)*\n2. **Drill into the numbers.** Cross-filter by therapeutic class to see where rebate capture is leaking against expected, and where spend concentrates. *(See \"Dashboards\".)*\n3. **Go to source.** Open the underlying **datasets** to inspect the Rx-claims and formulary rows, check lineage, and export a slice for finance. *(See \"Datasets\".)*\n4. **Build reconciled analysis.** Query the **pbm_core** semantic model directly (rebate_capture_rate, plan_paid_per_member, generic_dispensing_rate against the ≥90% GDR benchmark) so every figure ties back to a governed measure. *(See \"Semantic models\".)*\n5. **Track usage & exports.** Your role includes usage reporting — export what the business needs and let the **bell** flag when refreshed data lands. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Clinical Director",
      "tagline": "Own the desk — hold four-eyes approval, assign work, and govern the models.",
      "usesCapabilities": [
        "approvals",
        "worklist",
        "case-cockpit",
        "pipelines",
        "dashboards"
      ],
      "steps": "You run the clinical review operation. You're the one who holds **approve**, so a pharmacist's proposed determination becomes real only when you sign off.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the copilot's reasoning and evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored — that's the four-eyes guarantee, and it matters most on a denial. *(See \"Approvals & four-eyes\".)*\n2. **Approve in bulk where safe.** For clean, high-confidence PAs you can approve as a batch — but adverse determinations and controlled-substance PAs stay one-at-a-time. *(See \"Approvals & four-eyes\".)*\n3. **Balance the queue.** From the **worklist**, watch expedited PAs against the 24-hour clock and reassign to keep anything from breaching the coverage-determination window. *(See \"Your worklist\" and \"Working a case\".)*\n4. **Govern the model.** The pack ships an Rx PA auto-decision classifier as a training **pipeline** — it only ever recommends. Review its runs and, when it earns trust, approve the promotion so it can assist pharmacists. *(See \"Pipelines\".)*\n5. **Manage the desk at scale.** Use the **PA Operations** and **Trend & Safety** dashboards to spot turnaround drift and DUR hotspots, then act through assignment and approval. *(See \"Dashboards\".)*"
    },
    {
      "roleName": "PBM Compliance Officer",
      "tagline": "Read-only oversight — verify every determination was made, documented, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk followed CMS Part D and the plan's UM criteria — that every determination is documented, evidenced, and made by the right people.\n\n1. **Review determined cases.** Open **Cases** and inspect closed PA and DUR reviews: the disposition, the required **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Confirm the denial trail.** Every *Rx PA denied* must be a human pharmacist decision with a documented rationale — verify the note and the approver. *(See \"Working a case\".)*\n3. **Check the evidence.** On each case, confirm the clinical documentation is attached and was cited in the reasoning. *(See \"Evidence\".)*\n4. **Monitor at scale.** Use the **PA Operations** dashboard to watch turnaround against the 72h/24h windows and the **Trend & Safety** dashboard for DUR alert handling — flag outliers for a closer look. *(See \"Dashboards\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log, which you can read and export; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

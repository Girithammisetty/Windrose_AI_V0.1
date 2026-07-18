import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/mortgage-loss-mitigation/). */
export const mortgageLossMitigationGuide: PackGuide = {
  "packName": "mortgage-loss-mitigation",
  "displayName": "Mortgage Loss Mitigation",
  "summary": "AI-assisted **mortgage-servicing loss-mitigation** workflow for US servicers, subservicers, banks, and credit unions. It handles hardship-application intake with **RESPA Reg X regulatory-clock awareness** (the 5-business-day acknowledgment, the 30-day complete-application evaluation, and appeal windows), enforces **dual-tracking holds**, and evaluates workout options in the **investor-prescribed waterfall order** (GSE / FHA / VA / portfolio) — through denial-appeal handling with independent review.\n\nIt ships the dashboards, semantic model, dispositions, AI agents, grounding memories, and training pipelines to run the whole default-servicing desk — with every final Reg X determination, denial, and write-back staying proposal-mode under **four-eyes** human approval.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded loss-mit worklist (queue) carrying Reg X deadline clocks and dual-track status",
        "Five dispositions: approve workout offer, deny with specific reasons (appeal rights attach), close incomplete — missing documents, refer to foreclosure alternatives (short sale / DIL), close — loan reinstated",
        "Every disposition requires a documented note; final determinations route to the Loss Mitigation Manager as approver"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"lossmit_core\" semantic model (workout approval rate, denial rate, doc-completion rate, deadline runway, dual-track holds, arrearage exposure, backlog aging)",
        "Three dashboards: Loss Mitigation Command Center, Reg X Clock & Dual-Track Watch, Workout Outcomes",
        "Seeded verified and saved queries over the governed model"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A mortgage loss-mit intake triage copilot and a loss-mit operations analytics agent (Core agents specialized via tenant config)",
        "Reg X + investor-waterfall grounding memories",
        "Distressed-loan anomaly (isolation forest) and workout-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Loss Mitigation Specialist",
      "tagline": "First touch — triage hardship applications and start the Reg X clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: new loss-mitigation applications land in your queue, and the Reg X clock — the 5-business-day acknowledgment and the 30-day complete-application evaluation — starts ticking the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest regulatory clocks sort to the top; a timely complete application is a hold on the foreclosure track, so runway matters. *(See \"Your worklist\".)*\n2. **Open an application.** You get the **decision cockpit**: borrower, loan, arrearage and delinquency bucket, hardship reason, document status, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the file and any **evidence**, applies the Reg X and investor-waterfall grounding, and drafts a recommended disposition with cited reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Ground it in the documents.** Attach the borrower's hardship package — pay stubs, hardship letter, financials — so the recommendation cites the real file. *(See \"Evidence\".)*\n5. **Record your disposition.** If the borrower qualifies under the waterfall, propose *Approve workout offer*; if the file is still short, *Close incomplete — missing documents* after the follow-up chase; if the borrower cured, *Close — loan reinstated*. A note is required.\n6. **Hand off.** Your disposition becomes a **proposal** the Loss Mitigation Manager approves — you cannot approve your own. Watch the **bell** for assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Underwriting Reviewer",
      "tagline": "The deeper read — run the investor waterfall and independent appeal reviews.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You take the files that need the deeper evaluation: the full investor-waterfall / NPV read, and — critically — denial appeals, which must be reviewed by someone not involved in the original decision.\n\n1. **Pick up your reviews.** Filter **Cases** to your assignments and to appeals. Because you review independently of the first decision, appeals route here, not back to the original specialist. *(See \"Your worklist\".)*\n2. **Work the waterfall on the cockpit.** Evaluate every option the investor makes available, in the prescribed order — never steer for servicer convenience. Review the **evidence**, and use the Copilot to summarize the file and any new appeal evidence with citations. *(See \"Working a case\" and \"Evidence\".)*\n3. **Check re-eligibility.** For a repeat request after a broken trial plan, confirm the investor's re-eligibility rules before evaluating. The Copilot surfaces the borrower's prior applications and denials. *(See \"The Copilot\".)*\n4. **Decide with specifics.** Propose *Approve workout offer* with the qualifying evidence, or *Deny with specific reasons* — the note must state the specific reasons and any investor requirement relied upon, because appeal rights attach. If retention is not viable, *Refer to foreclosure alternatives*.\n5. **Watch outcomes at scale.** Open the **Workout Outcomes** dashboard to see denials by hardship reason and the product mix behind your reviews. *(See \"Dashboards\".)*\n6. **Every proposal still needs the Manager's approval** — your review sharpens the decision, it doesn't finalize it."
    },
    {
      "roleName": "SPOC Coordinator",
      "tagline": "Continuity of contact — own the borrower relationship and chase the documents.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "notifications",
        "dashboards"
      ],
      "steps": "You are the single point of contact. You keep the borrower's file moving and complete so the specialists can evaluate it before the clock runs out.\n\n1. **Own your borrowers.** Filter **Cases** to your assigned files and watch which are incomplete against the 5-business-day acknowledgment and the 30-day evaluation window. *(See \"Your worklist\".)*\n2. **See where the file stands.** On the cockpit, check document status and the deadline clock — what's missing and how much runway is left. *(See \"Working a case\".)*\n3. **Run the document chase.** As borrowers return items, attach them to the case so the file becomes complete and the evaluation can proceed on grounded evidence. *(See \"Evidence\".)*\n4. **Track the runway.** Open the **Reg X Clock & Dual-Track Watch** dashboard to see deadline runway and document-completeness mix across your book, and prioritize the chases with the least slack. *(See \"Dashboards\".)*\n5. **Stay ahead of assignments.** The **bell** flags new applications and approaching deadlines so nothing goes stale. *(See \"Notifications\".)*\n6. **Propose when appropriate** — e.g. *Close incomplete — missing documents* after a documented, exhausted follow-up — but the Loss Mitigation Manager approves the outcome. *(See \"Working a case\".)*"
    },
    {
      "roleName": "Loss Mitigation Manager",
      "tagline": "Own the desk — approve every determination, watch the clocks, run the numbers.",
      "usesCapabilities": [
        "approvals",
        "worklist",
        "dashboards",
        "case-cockpit",
        "semantic-models"
      ],
      "steps": "You run the loss-mit desk. You alone hold **approve**, so a proposed workout, denial, or referral becomes real only when you sign off — the four-eyes control on every Reg X determination.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **cannot** approve a proposal you authored — and a denial without specific reasons is a denial you send back. *(See \"Approvals & four-eyes\".)*\n2. **Guard the regulatory clock.** Open the **Reg X Clock & Dual-Track Watch** dashboard for deadline runway and dual-track holds by investor, then reassign from the **worklist** so nothing breaches. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Run the program numbers.** The **Loss Mitigation Command Center** dashboard shows backlog aging, disposition mix, and intake trend; ask the analytics Copilot follow-ups against the **lossmit_core** semantic model. *(See \"Dashboards\" and \"Semantic models\".)*\n4. **Balance the load.** Use worklist filters to spot bottlenecks and rebalance across intake, underwriting review, and the SPOC document chase. *(See \"Your worklist\".)*\n5. **Keep the trail clean.** Every approval you make is captured in the audit log — the evidence your Compliance Auditor and any exam will rely on."
    },
    {
      "roleName": "Servicing Compliance Auditor",
      "tagline": "Read-only oversight — verify every determination was made, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight for exam readiness. Your job is to confirm the desk followed RESPA Reg X and the investor guidelines — and that every determination is evidenced and four-eyed. You can see everything and change nothing.\n\n1. **Review determined files.** Open **Cases** and inspect closed applications: the disposition, the required **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Test denial specificity.** For every *Deny with specific reasons*, confirm the note states the specific reasons and any investor requirement relied upon — the borrower's entitlement when appeal rights attach. *(See \"Working a case\".)*\n3. **Check the evidence trail.** Confirm the hardship-package documents are attached and were cited in the decision. *(See \"Evidence\".)*\n4. **Watch for patterns.** Use the **Workout Outcomes** and **Reg X Clock & Dual-Track Watch** dashboards to scan denial rates by hardship reason, deadline compliance, and dual-track holds for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You verify the loop closed correctly — you never touch the case yourself."
    }
  ]
};

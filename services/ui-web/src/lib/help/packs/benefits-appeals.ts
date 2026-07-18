import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/benefits-appeals/). */
export const benefitsAppealsGuide: PackGuide = {
  "packName": "benefits-appeals",
  "displayName": "Benefits Eligibility & Appeals",
  "summary": "AI-assisted **eligibility adjudication and appeals** for government benefits programs — unemployment insurance, SNAP, Medicaid eligibility, TANF, and state disability — for state/county agencies and government BPO contractors. It handles determination triage with **due-process and processing-deadline awareness** (written-notice and hearing rights, SNAP expedited/standard clocks, UI first-payment promptness), appeal-hearing packet prep, overpayment establishment and equity-and-good-conscience waiver review, and identity-fraud watch balanced against false-positive harm to legitimate claimants.\n\nEvery adverse action stays **proposal-mode with four-eyes approval** — no benefit denial, termination, overpayment, or fraud consequence is ever autonomous. The pack ships the semantic model, dashboards, AI agents, grounding, and training pipelines to run the whole desk.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded determination queue with processing-deadline clocks (SNAP expedited/standard, UI promptness, hearing-packet deadlines)",
        "Five dispositions: approve benefits, deny with written findings & appeal rights, request verification documents, refer to fraud investigation, close — withdrawn",
        "Every adverse action routed to a human examiner with the Program Integrity Manager as sole approver (four-eyes)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A “benefits_core” semantic model (approval/denial rate, verification-request & fraud-referral share, appeal-overturn share, determination-age backlog, deadline runway, overpayment exposure)",
        "Three dashboards: Determinations Command Center, Timeliness & Due Process, Integrity & Overpayments",
        "Claimant–program network analytics plus seeded verified & saved questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A benefits-adjudication triage copilot and a program-operations analytics agent (tenant-specialized)",
        "Due-process + program-rule grounding memories",
        "Claim-anomaly (isolation forest) and determination-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Eligibility Examiner",
      "tagline": "First touch — investigate determinations and propose an eligibility outcome under the clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of adjudication: eligibility determinations land in your queue, and the processing clock is already running.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest processing deadlines sort to the top — an expedited SNAP or UI-promptness clock waits for no one. *(See “Your worklist”.)*\n2. **Open a determination.** You get the **decision cockpit**: claimant, program, claim and determination ids, issue type, verification status, and the deadline clock. *(See “Working a case”.)*\n3. **Run the triage Copilot.** It reads the case and any attached **evidence**, applies the due-process and program-rule grounding, watches days-to-deadline, and drafts a recommended disposition with cited reasoning — as a **proposal**. *(See “The Copilot”.)*\n4. **Attach evidence** — pay stubs, employer statements, verification documents — so any finding is grounded in the real record, not an assumption. *(See “Evidence”.)*\n5. **Record your disposition.** *Approve benefits* when eligibility is confirmed; *Deny — written findings & appeal rights* only with specific, policy-cited reasons the claimant’s notice can state; *Request verification documents* when the record is genuinely inconclusive (no adverse action); *Refer to fraud investigation* on an intentional-misrepresentation pattern, without suspending the pending claim; *Close — withdrawn* if the claimant withdrew. A note is required on every one.\n6. **Never shortcut due process.** An address match or a wage mismatch alone is a signal to verify, never grounds to deny — check current documents first.\n7. **Hand off.** Your disposition becomes a **proposal** the Program Integrity Manager approves — you can’t approve your own adverse action. Watch the **bell** for assignments and deadline warnings. *(See “Notifications”.)*"
    },
    {
      "roleName": "Appeals Hearing Preparer",
      "tagline": "Assemble fair-hearing packets and evidence exports before the hearing deadline.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards",
        "datasets"
      ],
      "steps": "When a determination is appealed, you build the packet that goes to the hearing — complete, evidenced, and on time.\n\n1. **Pick up appeals.** Filter **Cases** to appeal-hearing prep and assign the ones you own; the fair-hearing packet deadline is the clock that matters here. *(See “Your worklist”.)*\n2. **Work the packet on the cockpit.** Confirm the original determination, the written findings, and the claimant’s hearing rights. *(See “Working a case”.)*\n3. **Assemble and export evidence.** Attach every supporting document to the case, then produce the evidence export the hearing requires — you hold export rights for exactly this. *(See “Evidence”.)*\n4. **Ground the narrative.** Use the Copilot to summarize the determination history and cite the documents, so the packet’s reasoning traces back to the record. *(See “The Copilot”.)*\n5. **Pull source data.** Read the underlying **datasets** and lineage when the packet needs the claim, wage, or verification history behind a figure. *(See “Datasets”.)*\n6. **Watch readiness at scale.** The **Timeliness & Due Process** dashboard shows appeal-overturn share and deadline runway — a fast read on hearing-packet backlog. *(See “Dashboards”.)*\n7. **Disposition and hand off.** Where evidence warrants conceding an appeal, propose *Approve benefits*; the Program Integrity Manager approves. Notes are required."
    },
    {
      "roleName": "Overpayment Analyst",
      "tagline": "Establish overpayments and review equity-and-good-conscience waivers — fault first.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "datasets"
      ],
      "steps": "You work the money side: whether an overpayment exists, who was at fault, and whether a waiver is warranted.\n\n1. **Work overpayment reviews.** Filter **Cases** to overpayment establishment and waiver reviews. *(See “Your worklist”.)*\n2. **Establish fault on the cockpit.** Review the determination, the claim history, and the **evidence** — an agency-error overpayment where the claimant reported correctly is a waiver candidate, not a debt to pursue. *(See “Working a case” and “Evidence”.)*\n3. **Check the feeds.** You have visibility into the wage and cross-match ingestion feeds — read the underlying **datasets** to confirm whether a mismatch is claimant misreporting or an employer payroll-reporting lag. *(See “Datasets”.)*\n4. **Ground the analysis.** Use the Copilot to weigh fault and the equity-and-good-conscience standard against the cited record. *(See “The Copilot”.)*\n5. **Propose the outcome.** *Approve benefits* to concede or waive, *Deny — written findings & appeal rights* only with specific documented grounds, or *Request verification documents* if the record is inconclusive. A note is required.\n6. **Hand off.** Establishing a debt is an adverse action — it becomes a **proposal** the Program Integrity Manager approves. You never establish a debt autonomously."
    },
    {
      "roleName": "Program Integrity Manager",
      "tagline": "Own the desk — approve every adverse action, watch timeliness, gate model promotions.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the desk and hold **approve** — proposals become real determinations only when you say so, and every adverse action is four-eyed through you.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **can’t** approve a proposal you authored — that’s the four-eyes wall on denials, overpayments, and fraud referrals. *(See “Approvals & four-eyes”.)*\n2. **Watch the clocks.** The **Timeliness & Due Process** dashboard shows determination backlog and deadline runway; reassign from the **worklist** to keep anything from breaching a processing or hearing standard. *(See “Dashboards” and “Your worklist”.)*\n3. **Watch integrity exposure.** The **Integrity & Overpayments** dashboard tracks fraud-referral share and overpayment exposure — a disparate-impact watch on denial and referral patterns. *(See “Dashboards”.)*\n4. **Spot-check on the cockpit.** Open any case to confirm the findings are specific and the notice-worthy reasons hold up. *(See “Working a case”.)*\n5. **Gate the models.** The claim-anomaly and determination-outcome **pipelines** train on human dispositions; you approve promotion, and no model output ever becomes an adverse action without an individualized human determination. *(See “Pipelines”.)*\n6. **Balance the load.** Use worklist filters to spot bottlenecks across examiners, hearing prep, and overpayment reviews."
    },
    {
      "roleName": "Program Audit Lead",
      "tagline": "Read-only oversight — improper-payment and fair-hearing audit readiness.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk followed due process and program rules — and that every adverse action was evidenced and four-eyed.\n\n1. **Review resolved determinations.** Open **Cases** and inspect closed determinations: the disposition, the required **note**, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See “Your worklist” and “Working a case”.)*\n2. **Confirm due process.** On denials, check that the written findings are specific and policy-cited — the reasons the claimant’s notice and any hearing rest on. *(See “Working a case”.)*\n3. **Monitor at scale.** Use the **Determinations Command Center** and **Timeliness & Due Process** dashboards to watch approval/denial rates, appeal-overturn share, and deadline compliance for outliers worth a closer look. *(See “Dashboards”.)*\n4. **Audit the models.** Review the training **pipelines**, runs, and evaluation trends behind the anomaly and outcome scorers — model governance is part of improper-payment readiness. *(See “Pipelines”.)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin “Audit and SIEM export”.)*\n\n> You can see everything and change nothing — that’s the point."
    }
  ]
};

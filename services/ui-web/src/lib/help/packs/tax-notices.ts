import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/tax-notices/). */
export const taxNoticesGuide: PackGuide = {
  "packName": "tax-notices",
  "displayName": "Tax Notices",
  "summary": "AI-assisted **corporate tax notice and exemption-certificate resolution** for corporate tax departments, compliance BPOs and firms, multi-state retailers and SaaS sellers, and payroll providers. It handles notice intake triage with **jurisdictional-deadline awareness** (a missed response window forfeits appeal rights), penalty-abatement work (first-time abatement and reasonable-cause grounding), post-Wayfair economic-nexus questionnaires, exemption/resale certificate audit remediation, information-mismatch (CP2000-style) responses, and duplicate-notice reconciliation.\n\nEvery response, payment, abatement filing, and registration stays **proposal-mode with four-eyes approval** — the AI copilot and analysts propose, the Tax Compliance Manager alone approves. The pack ships the dashboards, semantic model, grounding memories, and AI agents to run the whole desk.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded notice worklist (queue) with jurisdictional deadline clocks",
        "Five dispositions: abate/withdraw (agency error or reasonable cause won), pay (assessment valid), file amended return, request agency clarification, close duplicate notice",
        "Every response, payment, and abatement filing routed through four-eyes approval — no autonomous filing or payment, ever"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"tax_notices_core\" semantic model (abatement rate, pay-valid share, amended-return share, deadline runway, assessed-vs-abated exposure)",
        "Three dashboards: Tax Notice Command Center, Deadlines & Exposure, Root-Cause & Abatement",
        "Entity–jurisdiction nexus network analytics, plus seeded verified and saved queries"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A notice-intake triage copilot and a tax-notice-operations analytics agent",
        "IRS-practice and state-DOR grounding memories (abatement, Wayfair nexus, certificate rules)",
        "Account-anomaly and notice-outcome training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Tax Notice Analyst",
      "tagline": "First touch — log and triage incoming notices and propose a disposition before the clock runs.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: IRS, state DOR, and county/city notices land in your queue, and the response window starts forfeiting appeal rights the moment it's missed.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadline runway sorts to the top — a missed response date can let a proposed assessment go final by default. *(See \"Your worklist\".)*\n2. **Open a notice.** You get the **decision cockpit**: legal entity, tax account, jurisdiction, notice type, assessed amount, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any attached documents, applies the IRS-practice and state-DOR grounding, and drafts a recommended disposition with reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach the notice and supporting records** so the recommendation is grounded in the real documents — the notice PDF, filing history, prior notices on the account. *(See \"Evidence\".)*\n5. **Record your disposition.** For a strong reasonable-cause or agency-error case propose *Abated/withdrawn*; if the assessment is correct, *Pay*; if our own filing was wrong, *File amended return*; if the facts are unresolved, *Request agency clarification*; if it repeats an already-resolved liability, *Close — duplicate notice*. A note is required on every code.\n6. **Hand off.** Your disposition becomes a **proposal** the Tax Compliance Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Controversy & Abatement Lead",
      "tagline": "Work abatements and protests — deeper research, grounded requests, and the exposure numbers.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "datasets",
        "dashboards"
      ],
      "steps": "You take the notices that need real controversy work — penalty abatements, reasonable-cause arguments, and protests where the substance matters.\n\n1. **Pick up assigned notices.** Filter **Cases** to abatement and protest work, or reassign to yourself. *(See \"Your worklist\".)*\n2. **Build the abatement on the cockpit.** Review the compliance history, prior notices, and the assessed penalty-and-interest breakdown; use the Copilot to draft the reasonable-cause or first-time-abatement argument grounded in the actual account facts it must cite. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Assemble the evidence.** Attach the filing history, payment records, and correspondence the request will rely on. *(See \"Evidence\".)*\n4. **Do the research.** Author and run queries against the seeded datasets and lineage to confirm the facts, and export what you need for the written protest. *(See \"Datasets\".)*\n5. **Size the exposure.** Open the **Root-Cause & Abatement** dashboard to see abatement rate and assessed-vs-abated dollars, and cross-filter by root cause or jurisdiction to spot systemic issues worth fixing once. *(See \"Dashboards\".)*\n6. **Propose the outcome.** Record *Abated/withdrawn* with the grounded note, or send it toward *Pay* / *File amended return* if the substance doesn't hold. A second person — the Tax Compliance Manager — approves any filing."
    },
    {
      "roleName": "Sales Tax Specialist",
      "tagline": "Own the multi-state sales/use and certificate surface — nexus questionnaires and cert remediation.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "dashboards"
      ],
      "steps": "You own the sales-and-use side: post-Wayfair economic-nexus questionnaires, exemption/resale certificate audits, and the agency feeds that carry them.\n\n1. **Work your queue.** Filter **Cases** to nexus reviews and certificate-audit remediation. *(See \"Your worklist\".)*\n2. **Handle nexus questionnaires.** On the cockpit, review registration posture and where thresholds were crossed; the Copilot grounds the answer in the Wayfair and streamlined-sales-tax memories and flags voluntary-disclosure considerations. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Remediate certificates.** Attach the exemption/resale certificates under audit and confirm they're valid and on file, so a missing-cert exposure is grounded in the real documents. *(See \"Evidence\".)*\n4. **Watch incoming feeds.** New agency notices flow into your queue from the ingestion reads you can see; triage them the same way. *(See \"Your worklist\".)*\n5. **Track the surface.** Open the **Tax Notice Command Center** to watch pay-valid share and backlog by jurisdiction, and cross-filter to a state to see where certificate hygiene is driving repeat notices. *(See \"Dashboards\".)*\n6. **Propose the disposition** with the required note; the Tax Compliance Manager approves before anything is registered or filed."
    },
    {
      "roleName": "Tax Compliance Manager",
      "tagline": "Own the desk — approve every disposition and filing, and keep the deadline runway clear.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "notifications"
      ],
      "steps": "You run the desk. You alone hold **approve**, so a proposed response, payment, or abatement filing becomes real only when you say so — that's the four-eyes wall.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **Deadlines & Exposure** dashboard shows deadline runway and assessed exposure — reassign from the **worklist** to keep any statutory response window from breaching. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Spot systemic root causes.** Use the **Root-Cause & Abatement** dashboard to see whether repeated notices on one account point to an ERP rate feed, registration gap, or certificate problem worth a one-time fix instead of case-by-case responses. *(See \"Dashboards\".)*\n4. **Balance the load.** Filter the worklist to spot bottlenecks and reassign across intake, controversy, and sales-tax work. *(See \"Working a case\".)*\n5. **Stay ahead of deadlines.** Keep an eye on the **bell** for at-risk clocks and new proposals waiting on you. *(See \"Notifications\".)*\n\n> Nothing is filed, paid, or registered on your behalf without a human approval — yours."
    },
    {
      "roleName": "Tax Governance Auditor",
      "tagline": "Read-only oversight — verify every determination was grounded, evidenced, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight for exam and provenance readiness. Your job is to confirm the desk followed the rules and that every determination is evidenced and four-eyed — you change nothing.\n\n1. **Review resolved notices.** Open **Cases** and inspect closed notices: the disposition, the required **note**, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the notice, filing history, and certificates are attached and were cited in the determination. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use the dashboards to watch abatement rate, pay-valid share, amended-return share, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the models.** Confirm the account-anomaly and notice-outcome pipelines and their runs are governed and promotable only through review — the training lineage is yours to inspect.\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

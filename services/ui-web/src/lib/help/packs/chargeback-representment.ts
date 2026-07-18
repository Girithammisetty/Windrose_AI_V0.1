import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/chargeback-representment/). */
export const chargebackRepresentmentGuide: PackGuide = {
  "packName": "chargeback-representment",
  "displayName": "Chargeback Representment",
  "summary": "\nAI-assisted **chargeback representment** for merchants, PSPs/acquirers offering managed disputes, and marketplaces. It handles incoming-chargeback triage with **response-deadline awareness**, fight-versus-accept economics, and reason-code-matched **compelling-evidence** assembly (Visa CE 3.0 pre-screen, delivery confirmation, usage logs, policy disclosure) — grounded in Visa and Mastercard dispute rules.\n\nThe pack also flags friendly-fraud patterns for a block-list feed, deduplicates pre-dispute alerts (RDR/Ethoca-style) so nothing is refunded twice, and enforces pre-arbitration escalation discipline — plus the dashboards, dispositions, agents, and training pipelines to run the whole dispute-response desk.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded chargeback-response worklist (queue) with response-deadline clocks",
        "Five dispositions: represent (fight) with compelling evidence, accept liability & refund, escalate to pre-arbitration, flag friendly-fraud pattern, close as duplicate/already-resolved",
        "Case fields carried by display projection (chargeback, order, reason code, evidence, economics)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A “representment_core” semantic model (fight rate, win rate, net recovery, accept-liability share, monitoring-program mix)",
        "Three dashboards: Chargeback Response Center, Win Rates & Evidence, Program Health & Thresholds",
        "Verified and saved queries for the canonical program questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A chargeback-triage copilot (fight/accept + evidence) and a dispute-program analytics agent",
        "Visa/Mastercard network-rule and CE 3.0 grounding memories",
        "Two training pipelines: order-anomaly detector (isolation forest) and representment win-likelihood scorer (xgboost)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Dispute Response Analyst",
      "tagline": "First touch — triage incoming chargebacks and call fight-or-accept before the response clock runs out.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: incoming chargebacks land in your queue, and the acquirer's response deadline — materially shorter than the network window — is ticking from the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest response clocks sort to the top; a strong case filed late is a lost case. *(See \"Your worklist\".)*\n2. **Open a chargeback.** You get the **decision cockpit**: merchant, order, amount, reason code, AVS/CVV results, delivery status, the customer's history, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and the order **evidence**, applies the Visa/Mastercard grounding, pre-screens CE 3.0, weighs fight economics, and drafts a recommended disposition — as a **proposal**. *(See \"The Copilot\".)*\n4. **Check the evidence fit.** Confirm the evidence matches the reason family — carrier delivery confirmation for not-received, usage logs for digital goods, policy disclosure for cancellations. *(See \"Evidence\".)*\n5. **Record your disposition.** Propose *Represent — fight with compelling evidence* when the evidence fits, or *Accept liability — refund, fight not warranted* when evidence is weak or below the economics threshold. Add the required note citing the reason code and evidence.\n6. **Hand off.** Your disposition becomes a **proposal** the Program Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Evidence Specialist",
      "tagline": "Assemble the compelling-evidence package — order, carrier, and alert-feed reads that make the case winnable.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "copilot",
        "datasets"
      ],
      "steps": "You build the evidence packages the second presentment will cite — genuine, verifiable records only, never fabricated or inferred.\n\n1. **Pick up cases marked to fight.** Filter **Cases** to those dispositioned *Represent — fight with compelling evidence*. *(See \"Your worklist\".)*\n2. **Assemble the package on the cockpit.** Attach and organize the real supporting records — delivery confirmation to the AVS-matched address, usage/login logs, checkout policy disclosure, prior undisputed transactions for a CE 3.0 element match. *(See \"Evidence\".)*\n3. **Pull from the source data.** You have read access to the connected order/carrier/alert-feed **datasets** and connections to source the exact records. *(See \"Datasets\".)*\n4. **Let the Copilot check the match.** Ask it to confirm the assembled evidence qualifies under the reason code and CE 3.0 window, and to cite chargeback and order ids. *(See \"The Copilot\".)*\n5. **Dedup against pre-dispute rails.** If the order was already refunded via an RDR/Ethoca-style alert, record it and route to *Close — duplicate or already resolved via alert/refund* — never refund twice.\n6. **Record and hand off.** Your disposition becomes a **proposal** for the Program Manager; you can't approve your own."
    },
    {
      "roleName": "Pre-Arbitration Lead",
      "tagline": "Run escalations and deep analysis — decide which big-ticket wins are worth taking to pre-arbitration.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "dashboards",
        "copilot"
      ],
      "steps": "You take the hardest cases — issuer pre-arbitrations on big-ticket, strong-evidence disputes — and decide whether to continue the fight or stand down.\n\n1. **Work the escalation queue.** Filter **Cases** to pre-arb / arbitration and assign to yourself; you hold case assign and can reassign across the desk. *(See \"Your worklist\".)*\n2. **Analyze on the cockpit.** Review the full history, the assembled **evidence**, and the economics — pre-arb carries higher fees, so recovery has to justify the risk. *(See \"Working a case\" and \"Evidence\".)*\n3. **Look for patterns.** Open the **Win Rates & Evidence** dashboard and cross-filter by reason code or evidence type to see where continuing pays off and where it doesn't. *(See \"Dashboards\".)*\n4. **Use the Copilot for the argument.** Have it summarize the strongest evidence and ground the escalation decision in the network rules. *(See \"The Copilot\".)*\n5. **Decide.** Propose *Escalate to pre-arbitration / arbitration* on a strong big-ticket case, or send it back toward *Accept liability — refund* when the economics don't hold. Notes are required — they're the audit trail.\n6. **Hand off.** Even your escalation is a **proposal** the Program Manager approves."
    },
    {
      "roleName": "Dispute Program Manager",
      "tagline": "Own the desk — approve every disposition, watch the deadlines, and steer promotions.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "notifications"
      ],
      "steps": "You run the dispute-response program. You alone hold **approve**, so representment filings, liability acceptances, and pre-arb escalations become real only when you say so — four-eyes on every write-back.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **Chargeback Response Center** dashboard shows deadline runway and open exposure — reassign from the **worklist** to keep anything from breaching the acquirer deadline. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Track program health.** Open **Program Health & Thresholds** to watch fight rate, win rate, net recovery, and accept-liability share against your monitoring-program thresholds. *(See \"Dashboards\".)*\n4. **Balance the load.** Use worklist filters to spot bottlenecks and reassign across response, evidence, and pre-arb.\n5. **Bulk actions.** For large batches of similar chargebacks you can run and approve bulk dispositions — the four-eyes rule still holds.\n6. **Govern the models.** As win-likelihood models are retrained, you review and **approve promotions** so only vetted scoring reaches the desk. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Payments Compliance Auditor",
      "tagline": "Read-only oversight — verify every filing was evidenced, four-eyed, and network-rule compliant.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "dashboards",
        "datasets"
      ],
      "steps": "You have **read-only** oversight for PSP-audit and network-program readiness. Your job is to confirm the desk followed Visa/Mastercard rules — and that every decision is evidenced and four-eyed.\n\n1. **Review resolved cases.** Open **Cases** and inspect closed chargebacks: the disposition, the **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the compelling-evidence records are genuine, attached, and cited — no fabricated or inferred evidence. *(See \"Evidence\".)*\n3. **Verify the models and pipelines.** Inspect the training pipelines, experiment runs, and eval trends behind the win-likelihood and anomaly scoring, and trace dataset lineage. *(See \"Datasets\".)*\n4. **Monitor at scale.** Use the dashboards to watch win rate, net recovery, and monitoring-program thresholds for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

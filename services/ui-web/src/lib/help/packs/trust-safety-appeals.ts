import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/trust-safety-appeals/). */
export const trustSafetyAppealsGuide: PackGuide = {
  "packName": "trust-safety-appeals",
  "displayName": "Trust & Safety Appeals",
  "summary": "AI-assisted **trust-and-safety appeals adjudication** for online platforms — social/UGC networks, marketplaces, gaming, and dating apps operating under the **EU Digital Services Act** and **UK Online Safety Act**. It handles appeal intake with **complaint-handling deadline awareness**, classifier false-positive detection with overturn-rate feedback into enforcement quality, brigading / mass-report integrity screening, and compromised-account restore paths — always with a human making the final call.\n\nBecause DSA complaint decisions may **never be taken solely by automated means**, the AI only ever proposes: it drafts a recommended disposition and a statement-of-reasons, and a second person approves any restoration or enforcement change. The pack ships the case queue, dispositions, appeals-operations analytics, AI agents, and grounding memories to run the whole desk.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded appeals worklist (queue) with complaint-handling deadline runway",
        "Five dispositions: overturn & restore (enforcement error), uphold enforcement, partial-modify the action (proportionality), escalate to policy team, close as duplicate appeal",
        "Every determination requires a note — it feeds both the appellant's statement of reasons and the label that retrains the classifier"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "An \"appeals_core\" semantic model (overturn rate, uphold rate, escalation share, deadline runway, enforcement-source mix, brigading report counts)",
        "Three dashboards: Appeals Command Center, Overturn & Classifier Quality, DSA Compliance & Transparency",
        "Verified and saved queries plus account-enforcement network analytics"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "An appeals-triage copilot and an appeals-operations analytics agent, specialized to the trust-and-safety domain",
        "DSA / UK Online Safety Act grounding memories",
        "Enforcement-anomaly (isolation forest) and appeal-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Appeals Reviewer",
      "tagline": "First touch — triage incoming appeals and start the complaint-handling clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: appeals against enforcement decisions — content removals, account suspensions, demonetizations, ranking restrictions — land in your queue, and the DSA complaint-handling clock is already running.\n\n1. **Open your queue.** Sidebar → **Cases**. Appeals with the tightest deadline runway sort to the top — a determination deadline waits for no one. *(See \"Your worklist\".)*\n2. **Open an appeal.** You get the **decision cockpit**: the account, the enforced content, the original decision and policy version, the classifier score band, report counts, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any **evidence**, applies the DSA / Online Safety Act grounding, and drafts a recommended disposition with cited reasoning — as a **proposal**, never an action. *(See \"The Copilot\".)*\n4. **Attach evidence** — the appellant's statement, the content in question, session or classifier records — so the recommendation is grounded in the real record, not the report count. *(See \"Evidence\".)*\n5. **Record your disposition.** *Overturn & restore* when the evidence confirms an enforcement error; *Uphold* when the appeal lacks merit; *Partial — modify the action* when the violation stands but the penalty is disproportionate; *Escalate to policy team* for a satire / counter-speech / precedent question; *Close* a duplicate appeal. A note is required on every one.\n6. **Hand off.** Your disposition becomes a **proposal** the Operations Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Senior Policy Reviewer",
      "tagline": "Precedent-setting appeals and classifier-quality investigations.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "dashboards",
        "semantic-models"
      ],
      "steps": "You take the appeals that set precedent and the patterns that point at a broken classifier — the counter-speech misreads, the documentary-context removals, the high-confidence false positives.\n\n1. **Pick up the hard cases.** Filter **Cases** to precedent-setting or escalated appeals, and **assign** them across the team as needed. *(See \"Your worklist\".)*\n2. **Adjudicate on the cockpit.** Weigh the enforcement evidence against the account's history — prior enforcements, prior appeal outcomes, verified and monetized standing. Use the Copilot to summarize precedent and cite policy versions. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Hunt classifier false positives.** Open the **Overturn & Classifier Quality** dashboard and **click** a segment — a policy area, an enforcement source, a classifier score band — to cross-filter the rest and see where overturn rate is spiking. *(See \"Dashboards\".)*\n4. **Query the model directly.** Run governed questions against the **appeals_core** semantic model to quantify a suspected false-positive cluster before you act on it. *(See \"Semantic models\".)*\n5. **Decide.** Propose *Overturn & restore* with the corrected label (it routes back into classifier retraining), *Partial — modify the action*, or *Escalate to policy team* when the written policy genuinely doesn't resolve the question. Your note becomes the statement of reasons — make it specific."
    },
    {
      "roleName": "Escalations Specialist",
      "tagline": "Own report-integrity and policy-team escalations — brigading and compromised accounts.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You own the escalations that aren't really about the content: coordinated mass-report bursts, and clean accounts that suddenly went bad.\n\n1. **Work the escalation queue.** Filter **Cases** to your assignments and report-integrity / policy-gap escalations. *(See \"Your worklist\".)*\n2. **Screen for brigading.** On the cockpit, weigh the content on its merits and treat the report count as a signal, not a verdict — enforcement volume riding a coordinated report burst is a report-integrity problem, not a violation. Use connection and ingestion context where the pack surfaces it. *(See \"Working a case\".)*\n3. **Check the evidence trail.** A violation burst from a previously clean account coinciding with a credential change and new-geography logins reads as a **compromised account** — the remedy is restore-and-secure with strikes expunged, not punishment of the owner. Attach and cite the session evidence. *(See \"Evidence\".)*\n4. **Spot the pattern.** Open the **Appeals Command Center** and cross-filter by account or enforcement source to confirm whether this is one incident or a campaign. *(See \"Dashboards\".)*\n5. **Decide.** Propose *Overturn & restore* for a confirmed compromise or brigading-driven error, or *Escalate to policy team* when it needs a policy owner. Let the Copilot draft the reasoning, but the note — and the call — are yours. *(See \"The Copilot\".)*"
    },
    {
      "roleName": "Appeals Operations Manager",
      "tagline": "Own the desk — approve dispositions, watch the deadlines, govern model promotions.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the desk. You're the one who holds **approve**, so a proposed overturn, restoration, or strike expungement becomes real only when you say so — that's the DSA four-eyes guarantee.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **DSA Compliance & Transparency** dashboard shows deadline runway, escalation share, and enforcement-source mix — reassign from the **worklist** to keep any appeal from breaching its determination deadline. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Balance the load.** Use worklist filters to spot backlog aging across intake, policy review, and escalations, and rebalance from the cockpit. *(See \"Working a case\".)*\n4. **Govern the models.** The pack ships enforcement-anomaly and appeal-outcome training pipelines; a trained model can't go live until you **approve its promotion** — review the run, then promote. *(See \"Pipelines\".)*"
    },
    {
      "roleName": "Transparency & Audit Lead",
      "tagline": "Read-only oversight — DSA transparency reporting and audit readiness.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "evidence",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk met its DSA and Online Safety Act obligations — timely, non-arbitrary, evidenced, and never decided solely by machine — and to feed the transparency report.\n\n1. **Review resolved appeals.** Open **Cases** and inspect closed appeals: the disposition, the **note** that became the statement of reasons, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** Confirm each determination cites the actual enforcement findings and attached records — not the report count. *(See \"Evidence\".)*\n3. **Report at scale.** Use the **DSA Compliance & Transparency** and **Overturn & Classifier Quality** dashboards for overturn rate, uphold rate, escalation share, and deadline compliance — the figures that feed transparency reports and classifier-quality reviews. *(See \"Dashboards\".)*\n4. **Audit the model loop.** Inspect the training pipelines and their runs to verify corrected labels flow back into retraining under governance. *(See \"Pipelines\".)*\n5. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

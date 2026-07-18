/**
 * Pack overlay: card-disputes (Reg E / Reg Z card dispute + chargeback
 * adjudication). Personas match packs/card-disputes/rbac/roles.yaml exactly.
 * Each persona is a day-in-the-life that stitches the shared platform capability
 * articles (referenced by slug) into the dispute workflow.
 */
import type { PackGuide } from "../types";

export const cardDisputesGuide: PackGuide = {
  packName: "card-disputes",
  displayName: "Card Disputes",
  summary: `
AI-assisted **card dispute and chargeback** adjudication for US issuers (banks,
credit unions, fintech program managers). It handles dispute intake with
**regulatory-clock awareness** (Reg E provisional credit, Reg Z billing-error
windows), first-party **fraud** escalation, and network **chargeback** recovery
grounded in reason codes — plus the dashboards, decision rules, and AI agents to
run the whole desk.
`,
  ships: [
    {
      label: "Case queue & decisions",
      items: [
        "A seeded dispute worklist (queue) with regulatory clocks",
        "Five dispositions: resolve in cardholder's favor, deny (no error), file chargeback, escalate to fraud, close (merchant credited)",
        "A governed “Reg E dispute triage” decision table",
      ],
    },
    {
      label: "Analytics",
      items: [
        "A “disputes_core” semantic model (cardholder-favor rate, chargeback win rate, deadline runway, provisional-credit exposure)",
        "Three dashboards: Dispute Command Center, Regulatory Clock & Provisional Credit, Chargeback Recovery",
        "Cardholder–merchant network analytics",
      ],
    },
    {
      label: "AI & grounding",
      items: [
        "A dispute-intake triage copilot and a dispute-operations analytics agent",
        "Reg E / Reg Z and network-rule grounding memories",
        "Transaction-anomaly and dispute-outcome training pipelines",
      ],
    },
  ],
  personas: [
    {
      roleName: "Dispute Intake Analyst",
      tagline: "First touch — triage new disputes and start the regulatory clock.",
      usesCapabilities: ["worklist", "case-cockpit", "copilot", "evidence", "notifications"],
      steps: `
You are the front line: new card disputes land in your queue, and Reg E / Reg Z
clocks start ticking the moment they do.

1. **Open your queue.** Sidebar → **Cases**. Tightest clocks sort to the top —
   provisional-credit deadlines wait for no one. *(See “Your worklist”.)*
2. **Open a dispute.** You get the **decision cockpit**: cardholder, transaction,
   merchant, amount, reason, and the deadline clock. *(See “Working a case”.)*
3. **Run the triage Copilot.** It reads the case and any **evidence**, applies the
   Reg E/Z grounding, and drafts a recommended disposition with reasoning — as a
   **proposal**. *(See “The Copilot”.)*
4. **Attach evidence** if the cardholder sent statements or receipts, so the
   recommendation is grounded in the real documents. *(See “Evidence”.)*
5. **Record your disposition.** For a clear error, propose *Resolve in cardholder's
   favor*; if it smells like first-party fraud, *Escalate to fraud investigation*;
   if the merchant already fixed it, *Close — merchant credited*. Add the required
   note.
6. **Hand off.** Your disposition becomes a **proposal** the Operations Manager
   approves — you can't approve your own. Keep an eye on the **bell** for
   assignments and clock warnings.
`,
    },
    {
      roleName: "Fraud Investigator",
      tagline: "Dig into escalated disputes — confirm or clear first-party fraud.",
      usesCapabilities: ["worklist", "case-cockpit", "evidence", "copilot", "dashboards"],
      steps: `
You take the cases intake escalates — the ones that look like first-party or
friendly fraud.

1. **Pick up escalations.** Filter **Cases** to your assignments / the
   *Escalate to fraud* status. *(See “Your worklist”.)*
2. **Investigate on the cockpit.** Review the transaction history, the
   cardholder–merchant relationship, and the **evidence** attached to the case.
   Use the Copilot to summarize prior disputes and cite the documents.
   *(See “Working a case” and “Evidence”.)*
3. **Look for patterns.** Open the **Dispute Command Center** dashboard and
   **click** a segment (e.g. a merchant or reason code) to cross-filter the rest —
   a fast way to see if this cardholder or merchant is a repeat. *(See
   “Dashboards”.)*
4. **Decide.** Propose *Deny — no error found* when you can substantiate
   first-party fraud, or send it back toward *Resolve in cardholder's favor* /
   *File chargeback* if the dispute is legitimate. Notes are required and matter —
   they're your audit trail.
5. **Reassign** if it belongs with the chargeback desk instead.
`,
    },
    {
      roleName: "Chargeback Specialist",
      tagline: "Recover funds from the merchant through the network chargeback flow.",
      usesCapabilities: ["case-cockpit", "evidence", "dashboards", "copilot"],
      steps: `
When the issuer eats a loss it can recover, you drive the network **chargeback**.

1. **Work the chargeback queue.** Filter to disputes dispositioned *File
   chargeback*.
2. **Build the case.** On the cockpit, confirm the **reason code** and assemble the
   **evidence** the network requires; the Copilot grounds the reason-code choice in
   the network rules. *(See “Evidence” and “The Copilot”.)*
3. **Track recovery.** The **Chargeback Recovery** dashboard shows win rate and
   open exposure; cross-filter by reason code to see where you're winning and
   losing. *(See “Dashboards”.)*
4. **Disposition** the outcome and add the note. Wins and losses feed the
   dispute-outcome model, so the desk gets smarter about which chargebacks are
   worth filing.
`,
    },
    {
      roleName: "Dispute Operations Manager",
      tagline: "Own the desk — approve dispositions, tune the rules, watch the clocks.",
      usesCapabilities: ["approvals", "decision-tables", "dashboards", "worklist", "case-cockpit"],
      steps: `
You run the desk. You're the one who holds **approve**, so proposals become real
only when you say so.

1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the
   proposed disposition, who proposed it, and the reasoning/evidence. **Approve**
   to write it back, or **Reject** with a note. You **can't** approve a proposal
   you authored. *(See “Approvals & four-eyes”.)*
2. **Watch the clocks.** The **Regulatory Clock & Provisional Credit** dashboard
   shows deadline runway and provisional-credit exposure — reassign from the
   **worklist** to keep anything from breaching. *(See “Dashboards” and “Your
   worklist”.)*
3. **Tune the policy.** Open **Decision Tables → Reg E dispute triage**. Adjust the
   rules, **batch-evaluate** the draft against recent disputes to see what would
   change, then **submit** — a second reviewer publishes the new version. *(See
   “Decision tables”.)*
4. **Balance the load.** Use the worklist filters to spot bottlenecks and reassign
   across intake, fraud, and chargeback.
`,
    },
    {
      roleName: "Dispute Compliance Auditor",
      tagline: "Read-only oversight — verify every decision was made and evidenced correctly.",
      usesCapabilities: ["worklist", "case-cockpit", "evidence", "dashboards"],
      steps: `
You have **read-only** oversight. Your job is to confirm the desk followed Reg E/Z
and the network rules — and that every decision is evidenced and four-eyed.

1. **Review resolved cases.** Open **Cases** and inspect closed disputes: the
   disposition, the **note**, who proposed it, and who approved it. The
   proposer/approver being **different people** is the four-eyes proof. *(See
   “Working a case” and “Approvals & four-eyes”.)*
2. **Check the evidence trail.** On each case, confirm the supporting documents are
   attached and were cited. *(See “Evidence”.)*
3. **Monitor at scale.** Use the dashboards to watch cardholder-favor rate,
   chargeback win rate, and deadline compliance for outliers worth a closer look.
   *(See “Dashboards”.)*
4. **Rely on the audit trail.** Every action — proposals, approvals, edits — is in
   the tamper-evident audit log; your admin can stream it to your SIEM. *(See the
   admin “Audit and SIEM export”.)*

> You can see everything and change nothing — that's the point.
`,
    },
  ],
};

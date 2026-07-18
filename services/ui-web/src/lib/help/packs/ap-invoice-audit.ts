import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/ap-invoice-audit/). */
export const apInvoiceAuditGuide: PackGuide = {
  "packName": "ap-invoice-audit",
  "displayName": "AP Invoice Audit",
  "summary": "AI-assisted **accounts-payable invoice exception and fraud audit** for enterprise finance and AP shared services, procure-to-pay BPOs, and audit-recovery firms. It triages invoice exceptions with **payment-run deadline awareness** — three-way-match gaps, duplicate payments, price/quantity variances — and escalates BEC banking-change, shell-vendor, and split-invoicing fraud with out-of-band verification discipline.\n\nEvery payment block or release runs proposal-mode with **four-eyes approval** under SOX-grade controls, plus the semantic model, dashboards, grounding memories, and AI agents to run the whole AP controls desk.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded invoice-exception worklist (queue) with payment-run deadline runway",
        "Typed case fields: root cause, out-of-band verified, verification method, recovered amount, control approver",
        "Five dispositions: block payment (confirmed error/fraud), reject & return to vendor, release payment (cleared), close on partial vendor credit, escalate to fraud investigation"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "An \"ap_audit_core\" semantic model (block rate, release rate, recovered dollars, fraud-escalation share, deadline runway, duplicate/BEC/shell-vendor/split-invoicing counts, vendor-risk mix)",
        "Three dashboards: AP Exception Center, Fraud & Vendor Risk, Recovery & Controls",
        "Canonical verified queries and saved queries over the AP controls model"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "An exception-triage copilot and an AP-controls analytics agent (proposal-mode, four-eyes)",
        "Payment-controls and invoice-fraud grounding memories",
        "Two training pipelines: an invoice-anomaly detector (isolation forest) and an exception-outcome scorer (xgboost)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "AP Exception Analyst",
      "tagline": "First touch — triage invoice exceptions before the next payment run.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: invoice exceptions — three-way-match gaps, duplicates, price and quantity variances — land in your queue, and the payment-run clock is always ticking.\n\n1. **Open your queue.** Sidebar → **Cases**. Exceptions inside the next payment run or an early-payment-discount window sort to the top. *(See \"Your worklist\".)*\n2. **Open an exception.** You get the **decision cockpit**: vendor, invoice, PO/goods-receipt match result, amount, the fraud indicator, and the deadline runway. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any **evidence**, grounds itself in the payment-controls and invoice-fraud memories, and drafts a recommended disposition with row-level reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Ground it in the documents.** Attach the invoice image, PO, or goods-receipt so the recommendation cites the real evidence. *(See \"Evidence\".)*\n5. **Record your disposition.** For a confirmed duplicate or overbilling, propose *Block payment — error or fraud confirmed*; for a defective invoice, *Reject — return to vendor*; for an explained variance, *Release payment — exception cleared*. A note is required on every one.\n6. **Hand off.** Your disposition becomes a **proposal** the AP Controls Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Recovery Audit Analyst",
      "tagline": "Dig into duplicates and credits — author queries and drive dollar recovery.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "datasets",
        "dashboards",
        "copilot",
        "evidence"
      ],
      "steps": "You chase the money back: duplicate payments already disbursed and negotiated vendor credits. You have query-authoring and export power the intake analysts don't.\n\n1. **Work recovery cases.** Filter **Cases** to duplicate-review and credit-recovery exceptions; you can assign and re-run cases as you dig. *(See \"Your worklist\".)*\n2. **Prove the duplicate pair.** On the cockpit, line up the two invoices — one-character OCR-confusable invoice numbers, matching vendor, amount, and date are the resubmission signature. Attach both as **evidence**. *(See \"Working a case\" and \"Evidence\".)*\n3. **Author the recovery query.** Query the invoice register and exception **datasets** directly, then **export** the results for the recovery statement. *(See \"Datasets\".)*\n4. **Track the take.** Open the **Recovery & Controls** dashboard for recovered dollars by exception type and the trend line; cross-filter to see where recovery is concentrated. *(See \"Dashboards\".)*\n5. **Decide.** Propose *Close on partial vendor credit* when you've negotiated a credit memo, or *Block payment* if the duplicate hasn't disbursed yet. The recovered-amount field and note become the auditor's evidence.\n6. **Hand off.** Your disposition is a **proposal** the AP Controls Manager approves."
    },
    {
      "roleName": "Vendor Master Specialist",
      "tagline": "Own vendor master — verify banking-change and shell-vendor fraud out-of-band.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "datasets"
      ],
      "steps": "You own the vendor master and the verification discipline that stops BEC and shell-vendor fraud. A banking change accepted on a fraudster's word is a wire out the door.\n\n1. **Pick up verification cases.** Filter **Cases** to banking-change and shell-vendor exceptions. A banking-change request arriving just before a payment run, from a lookalike domain or a new contact, is the BEC signature. *(See \"Your worklist\".)*\n2. **Investigate on the cockpit.** Review vendor tenure, TIN verification, sanctions screening, and the master-data change history alongside the request. The Copilot cites those vendor-master facts and flags the fraud pattern. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Verify out-of-band.** Call back on the known-good phone number already on the vendor master — never a number from the request. Record the **verification method** and the *out-of-band verified* field, and attach the confirmation as **evidence**. *(See \"Evidence\".)*\n4. **Check the source data.** You have visibility into the vendor-master **datasets** and ingestion connections to confirm what actually landed. *(See \"Datasets\".)*\n5. **Decide.** Propose *Escalate to fraud investigation* for a confirmed BEC or shell-vendor file, or *Release payment — exception cleared* once verification is documented. Note required.\n6. **Hand off.** Acceptance of any banking change is a **proposal** the AP Controls Manager approves — no vendor-master change moves on your say-so alone."
    },
    {
      "roleName": "AP Controls Manager",
      "tagline": "Own the desk — approve every payment block and release, watch the clocks.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the AP controls desk. You alone hold **approve**, so a proposed payment block, release, or banking-change acceptance becomes real only when you say so — that's the four-eyes control SOX auditors look for.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, the reasoning, and the evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the runway.** The **Recovery & Controls** dashboard shows deadline runway on the open book; reassign from the **worklist** so nothing breaches a payment run unreviewed. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Watch the risk mix.** The **Fraud & Vendor Risk** dashboard tracks open exceptions by fraud indicator, three-way-match gaps, and vendor-master compliance — cross-filter to find where controls are slipping. *(See \"Dashboards\".)*\n4. **Approve model promotions.** The invoice-anomaly and exception-outcome scorers retrain on real dispositions; you review and approve promotion before any model goes live. *(See \"Pipelines\".)*\n5. **Balance the load.** Use worklist filters to spot bottlenecks and reassign across the exception, recovery, and vendor-master analysts."
    },
    {
      "roleName": "Internal Controls Auditor",
      "tagline": "Read-only SOX oversight — verify every decision was evidenced and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight for SOX exam readiness. Your job is to confirm the desk followed payment controls — and that every block, release, and recovery is evidenced and four-eyed.\n\n1. **Review closed exceptions.** Open **Cases** and inspect resolved files: the disposition, the required **note**, the recovered-amount and out-of-band-verified fields, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the invoice, PO, goods-receipt, or verification callback is attached and was cited. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use the **AP Exception Center** and **Recovery & Controls** dashboards to watch block rate, release rate, recovered dollars, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the models.** Inspect the training **pipelines** and their runs to confirm scoring models were reviewed before promotion. *(See \"Pipelines\".)*\n5. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log; your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

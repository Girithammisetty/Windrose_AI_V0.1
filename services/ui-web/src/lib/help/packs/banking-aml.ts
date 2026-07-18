import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/banking-aml/). */
export const bankingAmlGuide: PackGuide = {
  "packName": "banking-aml",
  "displayName": "Banking AML",
  "summary": "AI-assisted **AML / BSA financial-crime** workflow for US banks. It runs alert triage with typology-grounded dispositions, **sanctions near-match** adjudication, and a **SAR-recommendation** queue where the recommendation and the actual filing decision are deliberately split — analysts recommend, the **MLRO** decides. Grounded in the BSA/FinCEN/OFAC regime (structuring, layering, sanctions, dormant-account reactivation, funnel accounts, peer-group anomalies).\n\nIt ships the datasets, KPI semantic model, dashboards, AI copilots, regulatory grounding, and training pipelines to run the whole financial-crime desk — while keeping every write in **proposal mode** so no SAR or CTR is ever filed autonomously.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded AML alert worklist (queue) covering structuring, layering, sanctions near-matches, and peer-group anomalies",
        "Five dispositions: clear as false positive, escalate to L2, recommend SAR (MLRO decides filing), sanctions true hit — block & report, EDD required",
        "A separation-of-duties model where SAR recommendation and SAR filing are held by different people"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "An \"aml_core\" semantic model (alert false-positive rate, SAR conversion rate, backlog aging, sanctions screening mix, transaction volumes)",
        "Three dashboards: AML Command Center, Sanctions & Screening, Transaction Monitoring",
        "Party relationship-network analytics for related-party expansion"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "An AML alert-triage copilot and a financial-crime analytics agent",
        "BSA / OFAC / SR 11-7 grounding memories, with explicit never-tip-off and SAR-confidentiality guardrails",
        "Two training pipelines: a transaction-anomaly detector (isolation forest) and an alert-disposition scorer (xgboost)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "AML Analyst L1",
      "tagline": "First-line triage — disposition alerts and, when the evidence is strong, recommend a SAR.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of the financial-crime desk: monitoring-system alerts land in your queue and you decide, quickly, whether each is noise or something worth building a case on.\n\n1. **Open your queue.** Sidebar → **Cases**. Aging and severity sort the backlog so the oldest, riskiest alerts surface first. *(See \"Your worklist\".)*\n2. **Open an alert.** The **decision cockpit** shows the party (KYC), the transactions, the scenario rule that fired, the typology, and the amount involved. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the alert and its evidence, applies the BSA/OFAC grounding, and drafts a recommended disposition — citing transaction ids, amounts, counterparties, and the customer's declared business — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence** where you have it — statements, wire details, prior correspondence — so the recommendation is grounded in real documents. *(See \"Evidence\".)*\n5. **Record your disposition.** *Clear as false positive* when the evidence contradicts the typology, *Escalate to L2* when it needs deeper case-building, or *Recommend SAR* when the typology evidence is strong and specific. A note is required — it captures the evidence the SAR narrative would cite.\n6. **Never tip off.** *Recommend SAR* is a recommendation only — the filing decision belongs to the MLRO. Never record or hint, on any customer-visible surface, that a SAR is contemplated. Watch the **bell** for assignments. *(See \"Notifications\".)*"
    },
    {
      "roleName": "AML Investigator L2",
      "tagline": "Deeper case-building — expand the network, weigh the typology, and route the decision.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "copilot",
        "dashboards"
      ],
      "steps": "You take what L1 escalates — the alerts that need real investigation: network expansion, adverse-media context, prior SAR history.\n\n1. **Pick up escalations.** Filter **Cases** to your assignments and the *Escalate to L2* status; you can also reassign work across the tier. *(See \"Your worklist\".)*\n2. **Build the case on the cockpit.** Review the transaction history and the related-party relationships, and read the evidence attached to the alert. Use the Copilot to summarize prior activity and cite the documents. *(See \"Working a case\" and \"Evidence\".)*\n3. **Look for patterns.** Open the **AML Command Center** dashboard and **click** a segment — a typology, a severity band, a month — to cross-filter the rest and see whether this party or counterparty is a repeat. *(See \"Dashboards\".)*\n4. **Decide and route.** Update the disposition: *Clear as false positive* if the case falls apart, *EDD required* when the risk needs enhanced due diligence, or *Recommend SAR* when the typology is substantiated. Notes are required — they are the audit trail.\n5. **Hand off to the right owner.** SAR filing decisions go to the MLRO; a sanctions near-match belongs with the Sanctions Analyst. Reassign accordingly."
    },
    {
      "roleName": "MLRO",
      "tagline": "The accountable officer — approve dispositions, decide SAR filings, own the program.",
      "usesCapabilities": [
        "approvals",
        "worklist",
        "case-cockpit",
        "dashboards",
        "notifications"
      ],
      "steps": "You are the Money Laundering Reporting Officer. Separation of duties routes every SAR decision to you alone — an analyst can *recommend* a SAR, but only you approve the disposition that acts on it, and only you decide whether it is filed.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence behind it. **Approve** to write it back, or **Reject** with a note. You **cannot** approve a proposal you authored — that is the four-eyes control. *(See \"Approvals & four-eyes\".)*\n2. **Own the SAR call.** For a *Recommend SAR* disposition, review the typology evidence the note captured before you approve — the filing decision is yours, and no autonomous filing is ever possible. *(See \"Working a case\".)*\n3. **Watch the program.** The **AML Command Center** dashboard tracks backlog aging, SAR conversion rate, and false-positive rate; use it to spot bottlenecks and reassign from the **worklist**. *(See \"Dashboards\" and \"Your worklist\".)*\n4. **Govern model promotions.** When the desk's alert-scoring model is put forward, you hold the promotion approval — after the Model Risk Validator has reviewed it. *(See \"Approvals & four-eyes\".)*\n5. **Stay on top of it.** The **bell** flags approvals waiting and cases nearing the regulatory clock. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Sanctions Analyst",
      "tagline": "Adjudicate sanctions near-matches — confirm a true hit or clear a false positive.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "dashboards"
      ],
      "steps": "You own the sanctions-screening queue: name-match alerts against OFAC and other watchlists, where the job is to distinguish a real sanctioned party from a coincidental name collision.\n\n1. **Work the screening queue.** Filter **Cases** to the *sanctions near-match* typology; the highest match scores rise first. *(See \"Your worklist\".)*\n2. **Adjudicate on the cockpit.** Weigh the distinguishing evidence — date of birth, address, ID number, jurisdiction — against the match score. The Copilot grounds the call in the OFAC screening rules and cites what it used. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Check the evidence.** Confirm the identity attributes and any supporting documents are attached before you decide. *(See \"Evidence\".)*\n4. **Decide.** Propose *Sanctions true hit — block & report* only when distinguishing evidence confirms the identity; otherwise *Clear as false positive* with the required note. Your disposition is a proposal the MLRO approves.\n5. **Track the screening mix.** The **Sanctions & Screening** dashboard shows hits by month and match-score distribution by verdict — cross-filter to see where near-matches cluster. *(See \"Dashboards\".)*"
    },
    {
      "roleName": "Model Risk Validator",
      "tagline": "Read-only model oversight — validate the AML models under SR 11-7 before they go live.",
      "usesCapabilities": [
        "pipelines",
        "datasets",
        "semantic-models",
        "dashboards"
      ],
      "steps": "You provide independent model-risk validation (SR 11-7). You review the models the desk relies on — the anomaly detector and the alert-disposition scorer — but you hold **no case-write power** and touch no customer decision.\n\n1. **Review the training pipelines.** Open **Pipelines** and inspect the transaction-anomaly (isolation forest) and alert-disposition (xgboost) templates and their runs — inputs, parameters, and metrics. *(See \"Pipelines\".)*\n2. **Trace the data.** Examine the underlying datasets and their profiles and lineage so you can attest to what the models were trained on. *(See \"Datasets\".)*\n3. **Read the governed metrics.** Use the **aml_core** semantic model and its measures to sanity-check model behavior against program KPIs like false-positive rate and SAR conversion. *(See \"Semantic models\".)*\n4. **Monitor at scale.** The dashboards let you watch alert and screening trends for drift worth a closer look. *(See \"Dashboards\".)*\n5. **Sign off, don't promote.** You document the validation; the MLRO holds the actual promotion approval. You can see everything and change nothing — that separation is the point."
    }
  ]
};

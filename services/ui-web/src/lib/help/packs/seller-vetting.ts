import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/seller-vetting/). */
export const sellerVettingGuide: PackGuide = {
  "packName": "seller-vetting",
  "displayName": "Marketplace Seller Vetting",
  "summary": "\nAI-assisted **marketplace seller vetting and counterfeit/IP-enforcement** adjudication for e-commerce marketplaces, app stores, resale platforms, and B2B marketplaces. It runs seller onboarding **KYB verification** with INFORM Consumers Act / EU DSA trader-traceability awareness, **counterfeit takedown** triage grounded in test-buy and signal-stack evidence, **DMCA/trademark claim** adjudication that weighs first-sale and claim-abuse defenses, **linked-account ring** detection, and reinstatement plan-of-action review.\n\nEvery final determination stays **proposal-mode with four-eyes approval** — the AI never removes a listing, suspends a seller, or promises an outcome to a claimant. It ships with a marketplace-integrity semantic model, dashboards, a disposition taxonomy, tenant-specialized AI agents, and training pipelines to run the whole trust-and-safety desk.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded vetting worklist (queue) with deadline-runway awareness",
        "Five dispositions: remove listing (violation confirmed), reject claim (insufficient evidence), suspend seller network (ring confirmed), request authenticity evidence, clear/reinstate seller",
        "A shared decision cockpit carrying case fields via display projection (typed case schemas deferred to pack-service)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "A \"seller_vetting_core\" semantic model (enforcement rate, claim-rejection rate, evidence-request share, reinstatement rate, ring-detection share, deadline runway, GMV at risk, KYB posture)",
        "Three dashboards: Marketplace Integrity Center, Counterfeit & IP Claims, Seller Risk & Rings",
        "Verified & saved canonical questions plus seller-category risk-surface analytics"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A case-triage vetting copilot and a marketplace-integrity analytics agent (tenant-specialized, proposal-mode)",
        "INFORM/DMCA/KYB grounding memories",
        "Listing-anomaly (isolation forest) and review-outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Vetting Analyst",
      "tagline": "First touch — triage new seller and enforcement reviews and start the response clock.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line: onboarding vetting, counterfeit takedowns, and IP claims land in your queue, and takedown-response, counter-notice, and verification deadlines start running the moment they do.\n\n1. **Open your queue.** Sidebar → **Cases**. The tightest deadline runway sorts to the top — a takedown-response or verification clock waits for no one. *(See \"Your worklist\".)*\n2. **Open a review.** You get the **decision cockpit**: seller, listing, brand/rights-holder, price-vs-MSRP band, KYB state, prior violations, and the deadline clock. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case and any **evidence**, applies the INFORM / DMCA / KYB grounding, and drafts a recommended disposition with row-level reasoning — as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence** — test-buy results, invoices, brand authentication, image-reuse captures — so the recommendation is grounded in the real documents, not a guess. *(See \"Evidence\".)*\n5. **Record your disposition.** For corroborated counterfeit/IP/safety, propose *Remove listing — violation confirmed*; if the brand claim fails its evidence burden, *Reject claim — insufficient evidence* (the note is your claim-abuse defense file); if you need invoices or a test buy first, *Request authenticity evidence*. Every disposition requires a note.\n6. **Hand off.** Your disposition becomes a **proposal** the Marketplace Trust Manager approves — you can't approve your own. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Marketplace Integrity Investigator",
      "tagline": "Dig into ring and fraud escalations — confirm linked-account networks with corroborated evidence.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "copilot",
        "evidence"
      ],
      "steps": "You take the linked-account and seller-fraud escalations — the ones where one shared attribute might be coincidence but a network of them is a ring.\n\n1. **Pick up escalations.** Filter **Cases** to your assignments and ring/fraud reviews; you can assign cases across the desk. *(See \"Your worklist\".)*\n2. **Investigate on the cockpit.** Review the seller's account age, payout banking, addresses, device fingerprints, and the **evidence** attached. Use the Copilot to summarize prior violations and cite the shared attributes. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Author queries and map the ring.** You have query-authoring and **export** rights for linkage analysis — pull the seller book, slice by shared attribute, and export the linkage set for the file. *(See \"Datasets\".)*\n4. **Look for patterns at scale.** Open the **Seller Risk & Rings** dashboard and **click** a segment — GMV tier, KYB mix, deep-discount category — to cross-filter the rest and spot the cluster. *(See \"Dashboards\".)*\n5. **Decide.** Propose *Suspend seller network — ring confirmed* only when multiple corroborated shared attributes line up; the required note must name every linkage. Reassign to the IP Claims Reviewer or back to intake if the evidence points elsewhere.\n6. **Hand off.** Your proposal goes to the Marketplace Trust Manager for four-eyes approval — no suspension executes without it."
    },
    {
      "roleName": "IP Claims Reviewer",
      "tagline": "Own brand-claim evidence intake — adjudicate DMCA and trademark claims, protecting lawful resellers.",
      "usesCapabilities": [
        "case-cockpit",
        "evidence",
        "copilot",
        "worklist"
      ],
      "steps": "You own the brand-claim book: DMCA notice-and-takedown for copyright and trademark claims where the first-sale doctrine protects genuine-goods resale.\n\n1. **Work your claim queue.** Filter **Cases** to IP claim reviews. Counter-notice and takedown-response deadlines drive your ordering. *(See \"Your worklist\".)*\n2. **Review the claim intake.** On the cockpit, check the claim basis, the rights-holder's evidence, and the incoming brand-registry / claim-intake connection records you can read. *(See \"Working a case\".)*\n3. **Build the evidence file.** Assemble the **evidence** — the claimant's proof of non-genuineness or material difference versus the seller's invoices, original photography, and complaint history. The Copilot weighs it both ways and cites the documents. *(See \"Evidence\" and \"The Copilot\".)*\n4. **Decide on the merits.** Propose *Remove listing — violation confirmed* when infringement is corroborated, or *Reject claim — insufficient evidence* when a brand claim against an established, market-priced seller fails its burden — resale of genuine goods is lawful unless the claimant evidences otherwise. The note records exactly why the evidence failed.\n5. **Hand off.** Your disposition is a **proposal**; the Marketplace Trust Manager approves. You propose, you never take the listing down yourself."
    },
    {
      "roleName": "Marketplace Trust Manager",
      "tagline": "Own the desk — approve dispositions, approve model promotions, watch the deadline runway.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the desk. You alone hold **approve**, so a listing removal, network suspension, or claim rejection becomes real only when you say so.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence. **Approve** to write it back, or **Reject** with a note. You **can't** approve a proposal you authored — that's the four-eyes rule. *(See \"Approvals & four-eyes\".)*\n2. **Watch the clocks.** The **Counterfeit & IP Claims** dashboard shows deadline runway on the open book; the **Marketplace Integrity Center** shows backlog aging and disposition mix. Reassign from the **worklist** to keep anything from breaching. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Handle volume.** You have bulk-execute and bulk-approve rights for well-evidenced batches — but the same evidence and note discipline applies to every case.\n4. **Govern the models.** Review and **approve promotions** for the listing-anomaly and review-outcome pipelines — trained models don't reach production without your sign-off. *(See \"Pipelines\".)*\n5. **Balance the load.** Use worklist filters to spot bottlenecks and reassign across intake, ring investigation, and IP claims."
    },
    {
      "roleName": "Marketplace Compliance Auditor",
      "tagline": "Read-only oversight — verify every determination was made, evidenced, and four-eyed for INFORM/DSA exam readiness.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight. Your job is INFORM Consumers Act / EU DSA exam readiness: confirm the desk followed the rules and that every determination is evidenced and four-eyed.\n\n1. **Review resolved cases.** Open **Cases** and inspect closed reviews: the disposition, the required **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** On each case, confirm the test-buy results, invoices, or claim evidence are attached and were cited in the determination. *(See \"Evidence\".)*\n3. **Trace the models.** You can read experiments, runs, promotions, and pipeline templates end to end — confirm which model version scored what, and that every promotion carried an approval. *(See \"Pipelines\".)*\n4. **Monitor at scale.** Use the dashboards to watch enforcement rate, claim-rejection rate, and deadline compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits, exports — is in the tamper-evident audit log your admin can stream to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

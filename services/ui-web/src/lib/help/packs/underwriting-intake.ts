import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/underwriting-intake/). */
export const underwritingIntakeGuide: PackGuide = {
  "packName": "underwriting-intake",
  "displayName": "Commercial Underwriting Intake",
  "summary": "AI-assisted **commercial underwriting submission intake and triage** for P&C carriers, MGAs/MGUs, wholesale brokers, and E&S markets. It runs the front of the funnel: **clearance-first** duplicate blocking (the first broker to submit a risk holds the market), appetite-fit and completeness triage across ACORD apps, currently-valued loss runs, and SOV/COPE detail, broker **needed-by deadline** awareness with renewal-defense prioritization, documented E&S specialty referral, and declination hygiene with specific risk-based reasons.\n\nEverything is grounded in fair-underwriting expectations — the copilot only ever *proposes*, and a second person approves any declination, referral, or write-back (four-eyes) — plus the submission-funnel dashboards, semantic model, and training pipelines to run the whole desk.",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded submission triage queue with broker needed-by deadline clocks",
        "Five dispositions: accept & route to underwriter, decline (out of appetite, documented reasons), request missing information, refer to specialty/E&S market, close (broker withdrew)",
        "A clearance-first / appetite / completeness / priority triage workflow"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "An \"underwriting_core\" semantic model (accept-to-underwriter rate, declination rate, info-request share, appetite & completeness mix, deadline runway, backlog aging)",
        "Three dashboards: Submission Intake Center, Appetite & Clearance, Broker & Funnel Performance",
        "Broker-line network analytics and verified/saved funnel questions"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A submission-intake triage copilot and an underwriting-operations analytics agent",
        "Clearance, appetite, E&S, cyber-controls, and fair-underwriting grounding memories",
        "Account-book anomaly (isolation forest) and submission-triage outcome (xgboost) training pipelines"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Submission Intake Analyst",
      "tagline": "First touch — triage new submissions clearance-first and propose an intake outcome.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front of the funnel: new broker submissions land in your queue, and every one has a needed-by clock and a market-holding clearance question the moment it arrives.\n\n1. **Open your queue.** Sidebar → **Cases**. Tightest broker deadlines and renewal-defense submissions sort to the top — speed-to-first-touch drives hit ratio. *(See \"Your worklist\".)*\n2. **Open a submission.** You get the **decision cockpit**: insured, account, broker and tier, line of business, loss history, TIV band, completeness flags, and the days-to-deadline runway. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case, applies the clearance/appetite/E&S grounding, and drafts a recommended disposition — clearance first, then appetite, then completeness — as a **proposal** with row-level reasoning. *(See \"The Copilot\".)*\n4. **Attach evidence** — the ACORD application, currently-valued loss runs, SOV/COPE, control attestations — so the recommendation is grounded in the real documents. *(See \"Evidence\".)*\n5. **Record your disposition.** *Accept — clear and route to underwriter* for an in-appetite, cleared, workable file; *Request missing information* naming the exact missing documents; *Refer to specialty / E&S market*; or *Decline — out of appetite* with specific, risk-based reasons in the required note.\n6. **Hand off.** Your disposition becomes a **proposal** the Underwriting Operations Manager approves — you can't approve your own declination or referral. Watch the **bell** for assignments and deadline warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Appetite & Clearance Specialist",
      "tagline": "Dig into clearance conflicts and hard appetite calls — author queries and export.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "dashboards",
        "semantic-models"
      ],
      "steps": "You take the submissions that aren't clean: duplicate clearance conflicts (who submitted first, is there a broker-of-record letter) and the borderline appetite calls that need a real look at the book.\n\n1. **Pick up the hard ones.** Filter **Cases** to your assignments and the clearance-conflict / borderline-appetite work. *(See \"Your worklist\".)*\n2. **Resolve clearance on the cockpit.** Verify received order and any broker-of-record letter before anything else — the first-in broker holds the market. Use the Copilot to summarize the conflicting submissions and cite the evidence. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Make the appetite call.** Open the **Appetite & Clearance** dashboard and **click** a segment (line, industry, TIV band, broker tier) to cross-filter the rest — a fast way to see where this risk sits against the published appetite. *(See \"Dashboards\".)*\n4. **Author the query when the dashboard isn't enough.** Compose against the **underwriting_core** semantic model, run it, and **export** the result for an appetite review. *(See \"Semantic models\".)*\n5. **Decide.** Propose *Refer to specialty / E&S market* with the documented admitted-market declination, *Decline — out of appetite* with risk-based reasons, or route a clean cleared risk to an underwriter. Notes are required and are your audit trail; a second approver publishes any declination or referral."
    },
    {
      "roleName": "Underwriting Assistant",
      "tagline": "Chase the missing broker documents and keep the intake feeds moving.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "datasets",
        "notifications"
      ],
      "steps": "You keep files moving toward workable: chasing the documents brokers still owe and watching that submissions actually land from the intake feeds.\n\n1. **Work the info-request queue.** Filter **Cases** to *Request missing information* — these are the files waiting on the broker. *(See \"Your worklist\".)*\n2. **Read what's missing.** On the cockpit, the note names the exact gap — currently-valued loss runs, SOV with COPE detail, a cyber controls supplemental — so you know precisely what to chase. *(See \"Working a case\".)*\n3. **Attach documents as they arrive.** As the broker returns paperwork, add it to the case so the file becomes appetite-callable and the Copilot can reason over the real documents. *(See \"Evidence\".)*\n4. **Watch the feeds.** Check the intake **datasets** and connection status so submissions aren't silently stuck upstream before they ever reach a queue. *(See \"Datasets\".)*\n5. **Update and re-route.** Move a now-complete file back into triage, and keep an eye on the **bell** for new assignments and deadline warnings so nothing ages past its needed-by date. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Underwriting Operations Manager",
      "tagline": "Own the desk — approve declinations and referrals, watch the funnel, govern promotions.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the intake desk. You're the one who holds **approve**, so a declination, referral, or any write-back becomes real only when you say so.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning/evidence — declinations and E&S referrals are four-eyes. **Approve** to write it back or **Reject** with a note; you **can't** approve a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Watch the funnel.** The **Submission Intake Center** and **Broker & Funnel Performance** dashboards show accept-to-underwriter rate, declination rate, info-request share, backlog aging, and deadline runway — reassign from the **worklist** to keep anything from breaching. *(See \"Dashboards\" and \"Your worklist\".)*\n3. **Balance the load.** Use worklist filters to spot bottlenecks and rebalance across intake, the appetite/clearance specialist, and the assistant. *(See \"Working a case\".)*\n4. **Govern the models.** The account-anomaly and submission-triage training **pipelines** produce scored models — you review and approve the promotion before any trained model informs the desk, in line with model-governance expectations. *(See \"Pipelines\".)*"
    },
    {
      "roleName": "Underwriting Audit Lead",
      "tagline": "Read-only oversight — market-conduct and fair-underwriting exam readiness.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "dashboards",
        "pipelines"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk followed fair-underwriting and surplus-lines diligent-search expectations — and that every intake decision is evidenced and four-eyed.\n\n1. **Review resolved submissions.** Open **Cases** and inspect closed files: the disposition, the **note**, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the decline and referral hygiene.** Confirm every *Decline — out of appetite* carries specific, risk-based reasons and every E&S referral carries the documented admitted-market declination — never anything that reads as unfair discrimination between risks of the same class. *(See \"Working a case\".)*\n3. **Monitor at scale.** Use the **Appetite & Clearance** and **Broker & Funnel Performance** dashboards to watch declination rate, appetite mix, and broker-tier patterns for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Trace the models.** Inspect the training **pipelines**, runs, and evaluation trends behind any promoted scorer — you can see the full model lineage but change none of it. *(See \"Pipelines\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

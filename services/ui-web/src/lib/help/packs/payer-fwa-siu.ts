import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/payer-fwa-siu/). */
export const payerFwaSiuGuide: PackGuide = {
  "packName": "payer-fwa-siu",
  "displayName": "Payer FWA & SIU",
  "summary": "AI-assisted **post-payment Fraud, Waste & Abuse (FWA)** detection and **Special Investigations Unit (SIU)** workflow for US health payers. It scores claim lines for FWA typologies (upcoding, NCCI unbundling, phantom billing, impossible-day units), profiles provider peer-group outliers with LEIE/OIG exclusion status, and runs an SIU lead queue from first review through investigation, recovery, and — where warranted — a two-signature DOJ/OIG/state-DOI referral.\n\nEvery determinative outcome is written back only under **four-eyes approval** and grounded in the billing-pattern evidence on the case. The pack reflects its regulatory context — HIPAA, the False Claims Act evidentiary standard, DOJ referral formatting, state DOI reporting, and chain-of-custody — and treats FWA scores strictly as decision support: **no claim is ever denied or recouped automatically.**",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded SIU lead queue (worklist) of FWA leads with priority and exposure",
        "Six investigation dispositions: open SIU investigation, referred (DOJ/OIG/state DOI, two-signature), recovery initiated, provider education letter, no findings (false positive), and pend for evidence",
        "Mandatory written basis on every determinative disposition (FCA evidentiary defensibility)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "Two governed semantic models: fwa_claims (hit rate, flagged-line mix, billed/paid, FWA score) and siu_operations (leads, confirmed vs false-positive, recovered dollars, exposure, cycle days)",
        "Two dashboards: SIU Command Center (leads by typology, pipeline status, recovered dollars, exposure, lead-source quality) and Provider Outlier Analytics (flag mix, billed/paid by specialty, specialty outlier grid)",
        "Seeded claim-line, lead, provider, and recovery datasets plus verified and saved queries"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "An SIU-tuned case-triage copilot that grounds recommendations in peer-group deviation, E/M distribution, NCCI pairs, and exclusion status — never accusing, always citing",
        "A payment-integrity analytics agent answering SIU KPI questions from the governed semantic models",
        "Two detector training pipelines: an isolation-forest claim-line outlier detector and an XGBoost lead-outcome classifier"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "SIU Investigator",
      "tagline": "Work FWA leads — investigate billing-pattern anomalies and propose an investigation disposition.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of the SIU: FWA leads land in your queue, and you turn a scored anomaly into a defensible investigation outcome.\n\n1. **Open your queue.** Sidebar → **Cases**. Leads sort by priority and exposure, so the highest-dollar, highest-signal work rises to the top. *(See “Your worklist”.)*\n2. **Open a lead.** You get the **decision cockpit**: the provider, the FWA typology (upcoding, unbundling, phantom billing, impossible-day), peer-group deviation, LEIE/OIG exclusion status, and prior related leads. *(See “Working a case”.)*\n3. **Run the triage Copilot.** It reads the case and its **evidence**, applies the SIU grounding, and drafts a recommended disposition with every factor cited — as a **proposal**. It describes findings as billing-pattern anomalies that warrant investigation; it never accuses a provider or member of fraud. *(See “The Copilot”.)*\n4. **Attach evidence.** Pull in the claim-line detail, peer comparisons, or complaint records so the recommendation is grounded in the real documents — the written basis matters for FCA defensibility. *(See “Evidence”.)*\n5. **Record your disposition.** Choose *Open SIU investigation* for a substantiated lead, *Pend — additional evidence needed* when it’s inconclusive, or *No findings — close as false positive* when the pattern clears. A note is mandatory on every determinative outcome.\n6. **Hand off.** Recovery and referral outcomes become **proposals** your Supervisor approves — you can’t sign off your own. Watch the **bell** for assignments and escalations. *(See “Notifications”.)*"
    },
    {
      "roleName": "SIU Supervisor",
      "tagline": "Own the desk — hold four-eyes approval over dispositions, bulk actions, and model promotions.",
      "usesCapabilities": [
        "approvals",
        "worklist",
        "case-cockpit",
        "dashboards",
        "notifications"
      ],
      "steps": "You run the SIU desk. You hold **approve**, so an investigator’s recommendation becomes a real recovery or referral only when you sign it.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the cited reasoning and evidence. **Approve** to write it back or **Reject** with a note. You **can’t** approve a proposal you authored — that’s the four-eyes guarantee. *(See “Approvals & four-eyes”.)*\n2. **Guard the high-stakes outcomes.** *Recovery initiated* requires your review and legal concurrence; a *Referred — DOJ/OIG/state DOI* disposition needs the senior-investigator + legal two-signature chain before it leaves the building. Nothing recoups a claim automatically.\n3. **Balance the load.** From the **worklist** you can assign and reassign leads across investigators to keep priority work moving and nothing aging out. *(See “Your worklist” and “Working a case”.)*\n4. **Watch the desk.** The **SIU Command Center** dashboard shows lead pipeline status, recovered dollars, and exposure by priority; **click** a segment to cross-filter and spot where leads are stalling. *(See “Dashboards”.)*\n5. **Approve model promotions.** When the Payment Integrity Analyst proposes promoting a retrained detector, that promotion routes to you for approval too — same governed sign-off as a case outcome. *(See “Approvals & four-eyes”.)*\n6. **Stay ahead of escalations.** Keep the **bell** in view for two-signature referrals and bulk actions waiting on you. *(See “Notifications”.)*"
    },
    {
      "roleName": "SIU Director",
      "tagline": "Program leadership — steer the unit by the numbers, assign work, and co-sign referrals.",
      "usesCapabilities": [
        "dashboards",
        "worklist",
        "case-cockpit",
        "approvals",
        "semantic-models"
      ],
      "steps": "You own the SIU program. Your day is the numbers, the workload, and the sign-offs that carry the unit’s name outside the building.\n\n1. **Start on the command center.** Open the **SIU Command Center** dashboard for hit rate, recovered dollars, exposure, and lead-source quality; open **Provider Outlier Analytics** for where the billing anomalies concentrate by specialty. **Share** or **export** these views for leadership and the board. *(See “Dashboards”.)*\n2. **Trust the definitions.** Every figure traces to the governed **fwa_claims** and **siu_operations** semantic models, so “recovered dollars” and “false-positive rate” mean the same thing everywhere. *(See “Semantic models”.)*\n3. **Direct the work.** From the **worklist** you assign leads and rebalance across investigators to hit cycle-time and coverage targets. *(See “Your worklist” and “Working a case”.)*\n4. **Co-sign the referrals.** A DOJ/OIG/state-DOI referral needs the two-signature chain; you’re one of the signatures that lets it proceed. *(See “Approvals & four-eyes”.)*\n5. **Close the loop.** Watch false-positive rate and recovery yield over time to decide where to point detection effort next quarter."
    },
    {
      "roleName": "Payment Integrity Analyst",
      "tagline": "Feed the funnel — profile providers, build the detectors, and shape the analytics the desk runs on.",
      "usesCapabilities": [
        "datasets",
        "pipelines",
        "semantic-models",
        "dashboards"
      ],
      "steps": "You supply the detection engine behind the SIU: the datasets, the models, and the dashboards that turn raw claims into ranked leads.\n\n1. **Explore the data.** In **Data**, profile the seeded claim-line, provider, lead, and recovery datasets to understand FWA flags, peer groups, and exclusion status before you build. *(See “Datasets”.)*\n2. **Train the detectors.** Run the pack’s two pipelines — the **isolation-forest** claim-line outlier detector (unsupervised statistical stage) and the **XGBoost** lead-outcome classifier (supervised scorer on labeled outcomes). Inspect the runs and their metrics. *(See “Pipelines”.)*\n3. **Propose a promotion.** When a retrained detector beats the incumbent, propose promoting it — an SIU Supervisor approves before it goes live, so no scoring change ships unreviewed. *(See “Approvals & four-eyes”.)*\n4. **Extend the analytics.** Author charts and dashboards against the **fwa_claims** and **siu_operations** semantic models — for example a new outlier grid by specialty or place of service — reusing the governed measures so numbers stay consistent. *(See “Semantic models” and “Dashboards”.)*\n5. **Frame it honestly.** Everything you surface is a detected billing-pattern anomaly and an SIU workflow outcome — never an adjudicated fraud determination."
    },
    {
      "roleName": "Compliance Auditor",
      "tagline": "Read-only oversight — verify every SIU decision was evidenced, four-eyed, and defensible.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "approvals",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the unit met the FCA evidentiary standard and the chain-of-custody rules — and that every determinative outcome was made by two people, not one.\n\n1. **Review closed leads.** Open **Cases** and inspect resolved investigations: the disposition, the mandatory **note**, who proposed it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See “Working a case” and “Approvals & four-eyes”.)*\n2. **Check the two-signature outcomes.** For any *Referred — DOJ/OIG/state DOI* disposition, confirm the senior-investigator + legal two-signature chain is present. *(See “Approvals & four-eyes”.)*\n3. **Trace the evidence.** On each case, confirm the supporting billing-pattern documents are attached and were cited in the basis. *(See “Evidence”.)*\n4. **Monitor at scale.** Use the **SIU Command Center** and **Provider Outlier Analytics** dashboards to watch false-positive rate, recovery yield, and outlier concentration for outliers worth a closer look. *(See “Dashboards”.)*\n5. **Rely on the audit trail.** Every action — proposals, approvals, edits, exports — is in the tamper-evident audit log, which your admin can stream to your SIEM. *(See the admin “Audit and SIEM export”.)*\n\n> You can see everything and change nothing — that’s the point."
    }
  ]
};

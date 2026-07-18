import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/post-acute-care/). */
export const postAcuteCareGuide: PackGuide = {
  "packName": "post-acute-care",
  "displayName": "Post-Acute Care",
  "summary": "\nAI-assisted review for **post-acute care** operators — home health, skilled nursing (SNF), and hospice. It brings the clinical and intake desks together around **PDGM/PDPM episode and assessment** analytics, referral intake, and 30-day rehospitalization risk, with a domain-grounded copilot that drafts recommendations and a second person who approves every write-back.\n\nReviewers work OASIS/MDS assessment flags, PDGM comorbidity and PDPM therapy-alignment optimizations, referral-triage decisions, and readmission-risk interventions — grounded in source documentation so every proposed code is **CERT/RAC/UPIC defensible**. The copilot **never** finalizes an assessment or makes an eligibility call; clinical staff and a manager always do (BR-1).\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded care-transition review queue: readmission-risk interventions, a PDGM comorbidity flag, a PDPM therapy-alignment flag, and hospice recertifications",
        "Eight dispositions — comorbidity codes accepted/rejected (BR-3), PDPM therapy plan realigned, readmission-risk intervention started / risk flag cleared, referral accepted / declined (rationale required), escalate to medical director",
        "Mandatory notes wherever the domain demands documented rationale (comorbidity evidence, therapy justification, referral declines)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "Two semantic models — pac_episodes (30-day rehospitalization rate, PDGM comorbidity capture, length of stay, OASIS/MDS completion time, CERT audit pass rate) and pac_referrals (acceptance rate, response latency)",
        "Three dashboards: Post-Acute Network, Readmission Watch, and Referral Intake",
        "Seed episode and referral datasets in the exact landing shape"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A post-acute care-transition triage copilot and a post-acute network analytics agent (specialized platform agents, proposal-mode + four-eyes)",
        "Domain grounding memories for OASIS/MDS, PDGM/PDPM, and hospice LCD context",
        "A 30-day rehospitalization risk training pipeline (random forest on the episode dataset) that feeds care-team prioritization only (BR-7)"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "HHA Clinical Nurse",
      "tagline": "Home-health clinical reviewer — validate assessments and act on readmission risk.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "dashboards"
      ],
      "steps": "You are the home-health clinical reviewer: OASIS assessment flags, PDGM comorbidity captures, and 30-day readmission-risk cases land in your queue.\n\n1. **Open your queue.** Sidebar → **Cases**. Higher-severity episodes and tighter review deadlines sort up. *(See “Your worklist”.)*\n2. **Open an episode.** The **decision cockpit** shows the patient, care setting, primary diagnosis, length of stay, and the readmission-risk score. *(See “Working a case”.)*\n3. **Run the Copilot.** It reads the case and any attached **evidence** (discharge summary, medication list, therapy notes), applies the post-acute grounding, and drafts a recommended disposition **with a citation for every proposed code** — as a proposal. *(See “The Copilot”.)*\n4. **Attach evidence** so each comorbidity assertion traces to source documentation — CERT/RAC/UPIC defensibility requires it. *(See “Evidence”.)*\n5. **Record your disposition.** Propose *PDGM comorbidity codes accepted* when the documentation supports it, *codes rejected* when it doesn’t (BR-3), *readmission-risk intervention started* or *risk flag cleared* for risk cases — or *escalate to medical director* when it needs a physician. Add the required note.\n6. **Hand off.** Your disposition becomes a **proposal** the Post-Acute Care Manager approves — you finalize the clinical judgment, they hold approve. Risk scores prioritize outreach only; they never justify a discharge decision (BR-7)."
    },
    {
      "roleName": "SNF MDS Coordinator",
      "tagline": "Skilled-nursing reviewer — MDS assessments and PDPM therapy alignment.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence"
      ],
      "steps": "You own the SNF side: MDS assessment reviews and PDPM therapy-alignment flags for residents.\n\n1. **Pick up SNF cases.** Filter **Cases** to your assignments — PDPM therapy flags and MDS Section GG reviews. *(See “Your worklist”.)*\n2. **Work the cockpit.** Review the resident’s PDPM group, diagnosis, and length of stay, and read the attached therapy notes as **evidence**. *(See “Working a case” and “Evidence”.)*\n3. **Run the Copilot** to summarize the assessment and draft an aligned therapy recommendation — grounded in the notes, and never proposing increases beyond documented clinical justification (BR-4). *(See “The Copilot”.)*\n4. **Record your disposition.** Propose *PDPM therapy plan brought into alignment* once the plan matches the documentation, *comorbidity codes accepted / rejected*, or *escalate to medical director*. Notes are your audit trail.\n5. **Hand off.** Your disposition is a **proposal**; the Post-Acute Care Manager approves the write-back — you can’t approve your own. You finalize the MDS assessment; the copilot never does (BR-1)."
    },
    {
      "roleName": "Intake Coordinator",
      "tagline": "Front door — triage inbound referrals and open the cases the desk works.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "notifications",
        "evidence"
      ],
      "steps": "You are the front door: referrals from acute discharge come to you first, and you open and route the cases the clinical reviewers work.\n\n1. **Open your queue.** Sidebar → **Cases**. New referrals and unassigned reviews surface here. *(See “Your worklist”.)*\n2. **Open a referral.** The **cockpit** shows the referring hospital, requested setting, diagnosis, and complexity — with any discharge paperwork attached as **evidence**. *(See “Working a case” and “Evidence”.)*\n3. **Run the triage Copilot** to get a drafted accept/decline recommendation grounded in the referral details and capacity fit — as a proposal. *(See “The Copilot”.)*\n4. **Record your disposition.** Propose *referral accepted — intake confirmed*, or *referral declined* — which **requires a documented rationale** (BR-6).\n5. **Open and assign** the downstream review case to the right clinical reviewer (home health nurse or MDS coordinator) so nothing waits. Your disposition becomes a **proposal** the Post-Acute Care Manager approves.\n6. **Watch the bell.** New referrals, assignments, and deadline warnings show up in **notifications** so you can keep the intake moving. *(See “Notifications”.)*"
    },
    {
      "roleName": "Post-Acute Care Manager",
      "tagline": "Own the desk — approve dispositions, watch the network, tune the model loop.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "pipelines"
      ],
      "steps": "You run the desk. You’re the one who holds **approve**, so a reviewer’s disposition becomes real only when you say so.\n\n1. **Clear the approvals inbox.** Sidebar → **Approvals**. Each item shows the proposed disposition, who proposed it, and the reasoning and evidence. **Approve** to write it back, or **Reject** with a note. You **can’t** approve a proposal you authored — that’s the four-eyes rule. *(See “Approvals & four-eyes”.)*\n2. **Watch the network.** The **Post-Acute Network**, **Readmission Watch**, and **Referral Intake** dashboards show rehospitalization rate, comorbidity capture, CERT audit pass rate, and referral acceptance — build or adjust views as the desk needs. *(See “Dashboards”.)*\n3. **Balance the load.** Reassign from the **worklist** and cockpit to keep high-risk episodes and time-sensitive referrals from slipping. *(See “Your worklist” and “Working a case”.)*\n4. **Keep the risk model current.** Review the **rehospitalization 30-day risk** training pipeline and its runs; the model feeds care-team prioritization only, never care denial (BR-7). *(See “Pipelines”.)*\n5. **Approve promotions.** When a retrained model is ready to move forward, you review and approve the promotion — a governed, second-person step just like case approvals."
    },
    {
      "roleName": "PAC Compliance Officer",
      "tagline": "Read-only oversight — confirm every decision was documented and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "evidence",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the desk stayed defensible — that every code traces to documentation and every decision was made by one person and approved by another.\n\n1. **Review resolved cases.** Open **Cases** and inspect closed reviews: the disposition, the mandatory **note**, who proposed it, and who approved it. The proposer and approver being **different people** is the four-eyes proof. *(See “Working a case” and “Approvals & four-eyes”.)*\n2. **Check the evidence trail.** Confirm each comorbidity, therapy, and referral-decline decision has its source documentation attached and cited — CERT/RAC/UPIC defensibility depends on it. *(See “Evidence”.)*\n3. **Monitor at scale.** Use the **Post-Acute Network** and **Readmission Watch** dashboards to spot outliers in comorbidity capture, CERT audit pass rate, and completion times worth a closer look. *(See “Dashboards”.)*\n4. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log, and eval trends are available for review; your admin can stream the log to your SIEM. *(See the admin “Audit and SIEM export”.)*\n\n> You can see everything and change nothing — that’s the point."
    }
  ]
};

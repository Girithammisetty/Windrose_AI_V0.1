import type { PackGuide } from "../types";

/* Auto-generated pack overlay (grounded in packs/care-management-medicare/). */
export const careManagementMedicareGuide: PackGuide = {
  "packName": "care-management-medicare",
  "displayName": "Medicare Care Management",
  "summary": "\nAI-assisted **Medicare care management** (CCM, PCM, TCM, BHI, CoCM, RPM, RTM, and APCM 2025) for provider practices, FQHCs, RHCs, and health-system ambulatory groups. It runs a **monthly billing review queue** gated on documentation completeness, drafts recommendations with a copilot grounded in the CMS billing rules, and surfaces enrollment-funnel and revenue-leakage KPIs plus RPM operations — so care teams enroll the right patients, bill only what's documented, and keep a RAC-audit-defensible trail.\n\nEvery billing decision is **proposal-mode with four-eyes approval** — autonomous billing is impossible by construction. The pack specializes the shared platform surfaces to the care-management domain; it does not change them.\n",
  "ships": [
    {
      "label": "Case queue & decisions",
      "items": [
        "A seeded monthly billing review queue — held/pending billing candidates plus drafted care-activity notes, care plans, and RPM review notes",
        "Eight dispositions: bill approved, hold for missing documentation, code adjusted before billing, consent issue confirmed (do not bill), care-activity note approved, care plan approved, RPM review signed, escalate to clinician reviewer",
        "Proposal-mode + four-eyes on every billing decision (BR-2: no autonomous billing)"
      ]
    },
    {
      "label": "Analytics",
      "items": [
        "Two semantic models: care_mgmt_core (enrollment rate, documentation completeness, RAC-audit completeness, expected-revenue leakage) and rpm_readings (RPM adherence, 16-day compliance)",
        "Three dashboards: Enrollment Funnel, Revenue Leakage, and RPM Operations",
        "Canonical KPI questions as verified & saved queries"
      ]
    },
    {
      "label": "AI & grounding",
      "items": [
        "A CMS-rule-grounded care-management triage copilot and a care-management operations analytics agent",
        "CMS-rule grounding memories: consent, code time minimums, same-month mutual exclusions, the RPM 16-day rule, TCM windows, FQHC/RHC G0511, and APCM bundling",
        "An enrollment-propensity training pipeline"
      ]
    }
  ],
  "personas": [
    {
      "roleName": "Care Manager RN",
      "tagline": "Work the monthly review queue — draft the billing and documentation call for every patient.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence",
        "notifications"
      ],
      "steps": "You are the front line of the care-management program: held and pending billing candidates, drafted activity notes, care plans, and RPM review notes land in your queue each month.\n\n1. **Open your queue.** Sidebar → **Cases**. Items nearing a billing deadline or with an expiring consent window sort up — the regulatory clock waits for no one. *(See \"Your worklist\".)*\n2. **Open a case.** You get the **decision cockpit**: patient, program (CCM/PCM/TCM/BHI/CoCM/RPM/RTM/APCM), the candidate code, logged time, and the documentation status. *(See \"Working a case\".)*\n3. **Run the triage Copilot.** It reads the case, applies the CMS grounding, and checks in order — active consent, code time minimums, same-month mutual exclusions, the RPM 16-day rule, TCM windows — then drafts a recommended disposition citing the exact missing artifact, as a **proposal**. *(See \"The Copilot\".)*\n4. **Attach evidence.** Pull in the consent record, encounter note, or device-reading log so the recommendation is grounded in the real documents. *(See \"Evidence\".)*\n5. **Record your disposition.** *Bill approved codes* when it's clean, *Hold for missing documentation* or *Consent missing or revoked — do not bill* when it isn't, or *Escalate to clinician reviewer* when it needs a clinical eye. Holds and adjustments require a note.\n6. **Hand off.** Your call becomes a **proposal** a clinician, director, or CFO approves — you can't bill on your own. Watch the **bell** for assignments and clock warnings. *(See \"Notifications\".)*"
    },
    {
      "roleName": "Care Manager LPN",
      "tagline": "Supporting care manager — prep cases and draft dispositions for review.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "copilot",
        "evidence"
      ],
      "steps": "You work alongside the RN care managers, taking cases through first review so nothing stalls before it reaches an approver.\n\n1. **Pick up your assignments.** Filter **Cases** to what's assigned to you and to items with the tightest documentation or consent windows. *(See \"Your worklist\".)*\n2. **Review on the cockpit.** Confirm the patient, program, and logged time, and read what the triage Copilot flagged — the specific rule or missing artifact it cites. *(See \"Working a case\" and \"The Copilot\".)*\n3. **Gather the documents.** Attach the consent record, encounter note, or RPM reading log the recommendation depends on. *(See \"Evidence\".)*\n4. **Draft the disposition.** Propose *Bill approved codes*, *Hold for missing documentation*, or *Care activity note approved* with a note where required. It's a **proposal** — an approver signs off.\n5. **Escalate when unsure.** If a case needs a clinical judgment call, disposition it *Escalate to clinician reviewer* so it lands with the MD reviewer."
    },
    {
      "roleName": "Clinician MD Reviewer",
      "tagline": "Clinical sign-off — approve the dispositions that need a licensed judgment.",
      "usesCapabilities": [
        "approvals",
        "case-cockpit",
        "copilot",
        "evidence"
      ],
      "steps": "You provide the clinical four-eyes: care managers draft, and you approve the calls that require a clinician — escalations, adjusted codes, and consent holds.\n\n1. **Clear escalations and approvals.** Sidebar → **Approvals**, plus Cases filtered to *Escalate to clinician reviewer*. Each item shows the proposed disposition, who drafted it, and the reasoning and evidence. *(See \"Approvals & four-eyes\".)*\n2. **Review clinically.** On the cockpit, verify the encounter documentation, the care plan, and the CMS rule the Copilot cited actually holds for this patient and program. *(See \"Working a case\" and \"Evidence\".)*\n3. **Decide.** **Approve** to let the disposition write back, or send it back with a note. You **can't** approve a proposal you authored — that's the four-eyes guarantee. *(See \"Approvals & four-eyes\".)*\n4. **Assign and adjust.** Reassign cases across the team, disposition *Code adjusted before billing* where a code should change, or *Care plan approved as plan of record* when the plan is sound.\n5. **Sign RPM reviews.** Approve *RPM review note signed* for remote-monitoring data reviews that meet the threshold."
    },
    {
      "roleName": "Director of Care Coordination",
      "tagline": "Run the desk — assign work, approve, and build the analytics the team runs on.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "worklist",
        "case-cockpit",
        "semantic-models"
      ],
      "steps": "You own the care-management program end to end: the queue, the approvals, and the dashboards everyone works from.\n\n1. **Balance the load.** From **Cases**, create and assign review cases across the RN and LPN care managers and keep an eye on aging documentation holds. *(See \"Your worklist\".)*\n2. **Approve at scale.** Clear the **Approvals** inbox — individually, or batch-approve a clean set — never approving your own proposals. *(See \"Approvals & four-eyes\".)*\n3. **Watch the program.** Open the **Enrollment Funnel** and **RPM Operations** dashboards to see where patients drop out of enrollment and where RPM adherence is slipping; **click** a segment to cross-filter the rest. *(See \"Dashboards\".)*\n4. **Author the metrics.** Build charts and dashboards on the **care_mgmt_core** and **rpm_readings** semantic models so KPIs like documentation-completeness and 16-day compliance are governed, not hand-computed. *(See \"Semantic models\" and \"Dashboards\".)*\n5. **Spot leakage early.** Use the Revenue Leakage view to find held-candidate patterns, then reassign from the worklist to keep documentation gaps from becoming lost revenue."
    },
    {
      "roleName": "Practice CFO",
      "tagline": "Own the revenue — approve billing, and track leakage and expected reimbursement.",
      "usesCapabilities": [
        "approvals",
        "dashboards",
        "semantic-models",
        "datasets"
      ],
      "steps": "You're accountable for care-management revenue and its compliance risk. You hold **approve**, so billing proposals become real only when you sign off.\n\n1. **Approve the billing.** Sidebar → **Approvals**. Each item shows the proposed code, the documentation behind it, and who drafted it. **Approve** to let it bill, or **Reject** with a note — and never on a proposal you authored. *(See \"Approvals & four-eyes\".)*\n2. **Track the money.** Open the **Revenue Leakage** dashboard: held-candidate count times reimbursement is your leakage signal, and total expected revenue tells you what's on the table this month. Note it's expected reimbursement, not posted revenue. *(See \"Dashboards\".)*\n3. **Query the KPIs.** Ask the care-management analytics agent, or run saved questions against **care_mgmt_core**, to see documentation-completeness and enrollment rates in plain operational terms. *(See \"Semantic models\".)*\n4. **Export for the board.** Pull dashboard and dataset exports for finance reporting. *(See \"Datasets\".)*"
    },
    {
      "roleName": "Compliance Officer",
      "tagline": "Read-only oversight — verify every bill was documented, consented, and four-eyed.",
      "usesCapabilities": [
        "worklist",
        "case-cockpit",
        "approvals",
        "dashboards"
      ],
      "steps": "You have **read-only** oversight. Your job is to confirm the program followed the CMS rules and stays RAC-audit-defensible — every billing decision documented, consented, and approved by someone other than its author.\n\n1. **Review decided cases.** Open **Cases** and inspect closed reviews: the disposition, the note, the cited CMS rule, who drafted it, and who approved it. Proposer and approver being **different people** is the four-eyes proof. *(See \"Working a case\" and \"Approvals & four-eyes\".)*\n2. **Check the evidence trail.** Confirm each billed case carries its consent record, encounter documentation, and — for RPM — the reading log, and that they were cited. *(See \"Evidence\".)*\n3. **Monitor at scale.** Use the dashboards to watch documentation-completeness, RAC-audit completeness, and 16-day compliance for outliers worth a closer look. *(See \"Dashboards\".)*\n4. **Rely on the audit trail.** Every proposal, approval, and edit is in the tamper-evident audit log, and your admin can stream it to your SIEM. *(See the admin \"Audit and SIEM export\".)*\n\n> You can see everything and change nothing — that's the point."
    }
  ]
};

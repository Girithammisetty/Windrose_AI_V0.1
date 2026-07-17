# Multi-tenant pack logins (manual testing)

Dev login at http://localhost:3000/login — enter the email, any password.
Each tenant is a fully isolated (RLS) vertical with its own users.

## Windrose Payer Claims Co  (`wr-payer`)
- tenant id: `019f6460-7827-7724-8d90-db30d401436d`
- workspace: `019f6460-7c59-7979-8097-04b58e68a9fb`
- packs: insurance-claims-payer

| login email | role |
|---|---|
| admin@payer.windrose | Tenant Admin (author) |
| approver@payer.windrose | Tenant Admin (four-eyes approver) |
| pa-nurse-reviewer@payer.windrose | PA Nurse Reviewer |
| medical-director@payer.windrose | Medical Director |
| appeals-analyst@payer.windrose | Appeals Analyst |
| payment-integrity-analyst@payer.windrose | Payment Integrity Analyst |
| compliance-officer@payer.windrose | Compliance Officer |
| member-services@payer.windrose | Member Services |

## Windrose Care Management  (`wr-caremgmt`)
- tenant id: `019f6461-9373-7f2a-a671-65e33d71affa`
- workspace: `019f6461-97a8-73b1-9dd4-2f8ac484d19d`
- packs: care-management-medicare

| login email | role |
|---|---|
| admin@caremgmt.windrose | Tenant Admin (author) |
| approver@caremgmt.windrose | Tenant Admin (four-eyes approver) |
| care-manager-rn@caremgmt.windrose | Care Manager RN |
| care-manager-lpn@caremgmt.windrose | Care Manager LPN |
| clinician-md-reviewer@caremgmt.windrose | Clinician MD Reviewer |
| director-of-care-coordination@caremgmt.windrose | Director of Care Coordination |
| practice-cfo@caremgmt.windrose | Practice CFO |
| compliance-officer@caremgmt.windrose | Compliance Officer |

## Windrose Provider RCM  (`wr-rcm`)
- tenant id: `019f6461-bb9c-779d-b209-36c68926d6d2`
- workspace: `019f6461-bfd8-7bcb-8905-2086370efe10`
- packs: healthcare-provider-rcm

| login email | role |
|---|---|
| admin@rcm.windrose | Tenant Admin (author) |
| approver@rcm.windrose | Tenant Admin (four-eyes approver) |
| biller@rcm.windrose | Biller |
| medical-coder@rcm.windrose | Medical Coder |
| denials-specialist@rcm.windrose | Denials Specialist |
| a-r-manager@rcm.windrose | A/R Manager |
| revenue-integrity-analyst@rcm.windrose | Revenue Integrity Analyst |
| revenue-cycle-director@rcm.windrose | Revenue Cycle Director |

## Windrose Payer FWA-SIU  (`wr-fwa`)
- tenant id: `019f6461-e3f8-75c4-b773-9123836c8033`
- workspace: `019f6461-e821-7cd9-ac57-d34ea4b8751d`
- packs: investigation-framework, payer-fwa-siu

| login email | role |
|---|---|
| admin@fwa.windrose | Tenant Admin (author) |
| approver@fwa.windrose | Tenant Admin (four-eyes approver) |
| investigator@fwa.windrose | Investigator |
| investigation-supervisor@fwa.windrose | Investigation Supervisor |
| evidence-custodian@fwa.windrose | Evidence Custodian |
| investigation-quality-reviewer@fwa.windrose | Investigation Quality Reviewer |
| siu-investigator@fwa.windrose | SIU Investigator |
| siu-supervisor@fwa.windrose | SIU Supervisor |
| siu-director@fwa.windrose | SIU Director |
| payment-integrity-analyst@fwa.windrose | Payment Integrity Analyst |
| compliance-auditor@fwa.windrose | Compliance Auditor |

## Windrose Pharmacy Benefits  (`wr-pbm`)
- tenant id: `019f6462-14dc-7249-8e1f-faa5e562b126`
- workspace: `019f6462-1925-7d0f-8046-a3694911b658`
- packs: pharmacy-benefit-mgmt

| login email | role |
|---|---|
| admin@pbm.windrose | Tenant Admin (author) |
| approver@pbm.windrose | Tenant Admin (four-eyes approver) |
| pa-pharmacist@pbm.windrose | PA Pharmacist |
| formulary-manager@pbm.windrose | Formulary Manager |
| rebate-analyst@pbm.windrose | Rebate Analyst |
| clinical-director@pbm.windrose | Clinical Director |
| pbm-compliance-officer@pbm.windrose | PBM Compliance Officer |

## Windrose Post-Acute Care  (`wr-pac`)
- tenant id: `019f6462-43af-7c19-83f8-feb3d4eee63e`
- workspace: `019f6462-47e5-79db-b828-8871ff03bcb0`
- packs: post-acute-care

| login email | role |
|---|---|
| admin@pac.windrose | Tenant Admin (author) |
| approver@pac.windrose | Tenant Admin (four-eyes approver) |
| hha-clinical-nurse@pac.windrose | HHA Clinical Nurse |
| snf-mds-coordinator@pac.windrose | SNF MDS Coordinator |
| intake-coordinator@pac.windrose | Intake Coordinator |
| post-acute-care-manager@pac.windrose | Post-Acute Care Manager |
| pac-compliance-officer@pac.windrose | PAC Compliance Officer |

## Windrose Banking AML  (`wr-aml`)
- tenant id: `019f6462-6b63-7a87-a307-5fda2e2e2ac1`
- workspace: `019f6462-7388-7b52-a1bd-882b060c29cd`
- packs: investigation-framework, banking-aml

| login email | role |
|---|---|
| admin@aml.windrose | Tenant Admin (author) |
| approver@aml.windrose | Tenant Admin (four-eyes approver) |
| investigator@aml.windrose | Investigator |
| investigation-supervisor@aml.windrose | Investigation Supervisor |
| evidence-custodian@aml.windrose | Evidence Custodian |
| investigation-quality-reviewer@aml.windrose | Investigation Quality Reviewer |
| aml-analyst-l1@aml.windrose | AML Analyst L1 |
| aml-investigator-l2@aml.windrose | AML Investigator L2 |
| mlro@aml.windrose | MLRO |
| sanctions-analyst@aml.windrose | Sanctions Analyst |
| model-risk-validator@aml.windrose | Model Risk Validator |

## Windrose Card Disputes  (`wr-disputes`)
- tenant id: `019f6a51-9089-7acd-a7ae-db015c85d0c0`
- workspace: `019f6a51-94ea-717a-912e-8c21357a50b7`
- packs: card-disputes

| login email | role |
|---|---|
| admin@disputes.windrose | Tenant Admin (author) |
| approver@disputes.windrose | Tenant Admin (four-eyes approver) |
| dispute-intake-analyst@disputes.windrose | Dispute Intake Analyst |
| fraud-investigator@disputes.windrose | Fraud Investigator |
| chargeback-specialist@disputes.windrose | Chargeback Specialist |
| dispute-operations-manager@disputes.windrose | Dispute Operations Manager |
| dispute-compliance-auditor@disputes.windrose | Dispute Compliance Auditor |

## Windrose Pharmacovigilance  (`wr-pv`)
- tenant id: `019f6a51-e8f1-790c-97a4-28c234c5e652`
- workspace: `019f6a51-ed51-77c8-a331-a7ddd5f1b751`
- packs: pharmacovigilance

| login email | role |
|---|---|
| admin@pv.windrose | Tenant Admin (author) |
| approver@pv.windrose | Tenant Admin (four-eyes approver) |
| pv-intake-specialist@pv.windrose | PV Intake Specialist |
| pv-medical-reviewer@pv.windrose | PV Medical Reviewer |
| pv-safety-officer@pv.windrose | PV Safety Officer |
| pv-signal-analyst@pv.windrose | PV Signal Analyst |
| pv-quality-auditor@pv.windrose | PV Quality Auditor |

## Windrose Workers Comp  (`wr-wcomp`)
- tenant id: `019f6a73-b6ea-7d88-ae23-8da115f46580`
- workspace: `019f6a73-bb44-79bc-bd20-2a166dba063e`
- packs: workers-comp-claims

| login email | role |
|---|---|
| admin@wcomp.windrose | Tenant Admin (author) |
| approver@wcomp.windrose | Tenant Admin (four-eyes approver) |
| wc-claims-adjuster@wcomp.windrose | WC Claims Adjuster |
| wc-nurse-case-manager@wcomp.windrose | WC Nurse Case Manager |
| wc-medical-bill-reviewer@wcomp.windrose | WC Medical Bill Reviewer |
| wc-claims-manager@wcomp.windrose | WC Claims Manager |
| wc-compliance-auditor@wcomp.windrose | WC Compliance Auditor |

## Windrose Trucking Claims  (`wr-trucking`)
- tenant id: `019f6a74-0870-7937-8d9a-8d43fca3b661`
- workspace: `019f6a74-0cb8-72f5-b6d9-3def85626d0f`
- packs: trucking-claims

| login email | role |
|---|---|
| admin@trucking.windrose | Tenant Admin (author) |
| approver@trucking.windrose | Tenant Admin (four-eyes approver) |
| claims-analyst@trucking.windrose | Claims Analyst |
| carrier-compliance-analyst@trucking.windrose | Carrier Compliance Analyst |
| safety-review-specialist@trucking.windrose | Safety Review Specialist |
| claims-safety-manager@trucking.windrose | Claims & Safety Manager |
| fleet-compliance-auditor@trucking.windrose | Fleet Compliance Auditor |

## Windrose Warranty Claims  (`wr-warranty`)
- tenant id: `019f6a74-32cc-7700-b9bf-d102bf802e29`
- workspace: `019f6a74-3720-7766-98eb-ec1beef2974c`
- packs: warranty-claims

| login email | role |
|---|---|
| admin@warranty.windrose | Tenant Admin (author) |
| approver@warranty.windrose | Tenant Admin (four-eyes approver) |
| warranty-claims-analyst@warranty.windrose | Warranty Claims Analyst |
| technical-assessor@warranty.windrose | Technical Assessor |
| supplier-recovery-specialist@warranty.windrose | Supplier Recovery Specialist |
| warranty-operations-manager@warranty.windrose | Warranty Operations Manager |
| warranty-audit-lead@warranty.windrose | Warranty Audit Lead |

## Windrose Loss Mitigation  (`wr-lossmit`)
- tenant id: `019f6a74-6849-7728-9207-35be40748365`
- workspace: `019f6a74-6c80-7e77-a754-362f18fb335b`
- packs: mortgage-loss-mitigation

| login email | role |
|---|---|
| admin@lossmit.windrose | Tenant Admin (author) |
| approver@lossmit.windrose | Tenant Admin (four-eyes approver) |
| loss-mitigation-specialist@lossmit.windrose | Loss Mitigation Specialist |
| underwriting-reviewer@lossmit.windrose | Underwriting Reviewer |
| spoc-coordinator@lossmit.windrose | SPOC Coordinator |
| loss-mitigation-manager@lossmit.windrose | Loss Mitigation Manager |
| servicing-compliance-auditor@lossmit.windrose | Servicing Compliance Auditor |

## Windrose Credit Disputes  (`wr-fcra`)
- tenant id: `019f6a74-98d8-787b-a76d-6bac9ef7c30f`
- workspace: `019f6a74-9943-7a0e-aff9-ae0e076fcdb0`
- packs: credit-disputes

| login email | role |
|---|---|
| admin@fcra.windrose | Tenant Admin (author) |
| approver@fcra.windrose | Tenant Admin (four-eyes approver) |
| dispute-investigator@fcra.windrose | Dispute Investigator |
| furnisher-data-analyst@fcra.windrose | Furnisher Data Analyst |
| identity-theft-specialist@fcra.windrose | Identity Theft Specialist |
| dispute-operations-manager@fcra.windrose | Dispute Operations Manager |
| fcra-compliance-auditor@fcra.windrose | FCRA Compliance Auditor |

## Windrose Background Screening  (`wr-screening`)
- tenant id: `019f6a74-c5c0-71ad-be3d-a6e11f590680`
- workspace: `019f6a74-c9f7-7b89-9147-d2549697f353`
- packs: background-screening

| login email | role |
|---|---|
| admin@screening.windrose | Tenant Admin (author) |
| approver@screening.windrose | Tenant Admin (four-eyes approver) |
| screening-adjudicator@screening.windrose | Screening Adjudicator |
| identity-resolution-specialist@screening.windrose | Identity Resolution Specialist |
| adverse-action-coordinator@screening.windrose | Adverse Action Coordinator |
| screening-operations-manager@screening.windrose | Screening Operations Manager |
| fcra-compliance-auditor@screening.windrose | FCRA Compliance Auditor |

## Windrose Trade Compliance  (`wr-trade`)
- tenant id: `019f6a73-e0af-7d6d-b381-1a26863a6a2e`
- workspace: `019f6a73-e0f9-7b4e-9c9a-a7b5404d9446`
- packs: trade-compliance

| login email | role |
|---|---|
| admin@trade.windrose | Tenant Admin (author) |
| approver@trade.windrose | Tenant Admin (four-eyes approver) |
| classification-analyst@trade.windrose | Classification Analyst |
| screening-analyst@trade.windrose | Screening Analyst |
| licensing-specialist@trade.windrose | Licensing Specialist |
| trade-compliance-manager@trade.windrose | Trade Compliance Manager |
| trade-audit-lead@trade.windrose | Trade Audit Lead |

## Windrose Trust & Safety  (`wr-appeals`)
- tenant id: `019f6a74-f27f-72f1-9489-932d4d188e80`
- workspace: `019f6a74-f6ce-7b1b-8194-07c91989d7dd`
- packs: trust-safety-appeals

| login email | role |
|---|---|
| admin@appeals.windrose | Tenant Admin (author) |
| approver@appeals.windrose | Tenant Admin (four-eyes approver) |
| appeals-reviewer@appeals.windrose | Appeals Reviewer |
| senior-policy-reviewer@appeals.windrose | Senior Policy Reviewer |
| escalations-specialist@appeals.windrose | Escalations Specialist |
| appeals-operations-manager@appeals.windrose | Appeals Operations Manager |
| transparency-audit-lead@appeals.windrose | Transparency & Audit Lead |

## Windrose Device Vigilance  (`wr-mdr`)
- tenant id: `019f6a75-0a20-7e92-8c4f-36c3f4f88c44`
- workspace: `019f6a75-0e71-75d0-892f-52b82da9d36a`
- packs: device-complaints

| login email | role |
|---|---|
| admin@mdr.windrose | Tenant Admin (author) |
| approver@mdr.windrose | Tenant Admin (four-eyes approver) |
| complaint-intake-coordinator@mdr.windrose | Complaint Intake Coordinator |
| complaint-investigator@mdr.windrose | Complaint Investigator |
| mdr-reportability-analyst@mdr.windrose | MDR Reportability Analyst |
| quality-regulatory-manager@mdr.windrose | Quality & Regulatory Manager |
| quality-systems-auditor@mdr.windrose | Quality Systems Auditor |

## Windrose Underwriting Intake  (`wr-uw`)
- tenant id: `019f6a75-5d07-7d56-b83a-fc2b75598d32`
- workspace: `019f6a75-613f-71a5-9296-8769e73f14c6`
- packs: underwriting-intake

| login email | role |
|---|---|
| admin@uw.windrose | Tenant Admin (author) |
| approver@uw.windrose | Tenant Admin (four-eyes approver) |
| submission-intake-analyst@uw.windrose | Submission Intake Analyst |
| appetite-clearance-specialist@uw.windrose | Appetite & Clearance Specialist |
| underwriting-assistant@uw.windrose | Underwriting Assistant |
| underwriting-operations-manager@uw.windrose | Underwriting Operations Manager |
| underwriting-audit-lead@uw.windrose | Underwriting Audit Lead |

## Windrose Merchant Disputes  (`wr-merchant`)
- tenant id: `019f6a75-b029-79f6-bfe9-337494ef122a`
- workspace: `019f6a75-b487-7a9e-9bb9-bbabac27e7ed`
- packs: chargeback-representment

| login email | role |
|---|---|
| admin@merchant.windrose | Tenant Admin (author) |
| approver@merchant.windrose | Tenant Admin (four-eyes approver) |
| dispute-response-analyst@merchant.windrose | Dispute Response Analyst |
| evidence-specialist@merchant.windrose | Evidence Specialist |
| pre-arbitration-lead@merchant.windrose | Pre-Arbitration Lead |
| dispute-program-manager@merchant.windrose | Dispute Program Manager |
| payments-compliance-auditor@merchant.windrose | Payments Compliance Auditor |

## Windrose Marketplace Integrity  (`wr-marketplace`)
- tenant id: `019f6a76-03c6-7c8e-9ba5-b6d939688877`
- workspace: `019f6a76-041c-73f3-9e60-08df10523dc0`
- packs: seller-vetting

| login email | role |
|---|---|
| admin@marketplace.windrose | Tenant Admin (author) |
| approver@marketplace.windrose | Tenant Admin (four-eyes approver) |
| vetting-analyst@marketplace.windrose | Vetting Analyst |
| marketplace-integrity-investigator@marketplace.windrose | Marketplace Integrity Investigator |
| ip-claims-reviewer@marketplace.windrose | IP Claims Reviewer |
| marketplace-trust-manager@marketplace.windrose | Marketplace Trust Manager |
| marketplace-compliance-auditor@marketplace.windrose | Marketplace Compliance Auditor |

## Windrose Benefits Adjudication  (`wr-benefits`)
- tenant id: `019f6a76-530d-7301-a968-51a719fb802d`
- workspace: `019f6a76-5759-78c2-b225-860d97a63d6a`
- packs: benefits-appeals

| login email | role |
|---|---|
| admin@benefits.windrose | Tenant Admin (author) |
| approver@benefits.windrose | Tenant Admin (four-eyes approver) |
| eligibility-examiner@benefits.windrose | Eligibility Examiner |
| appeals-hearing-preparer@benefits.windrose | Appeals Hearing Preparer |
| overpayment-analyst@benefits.windrose | Overpayment Analyst |
| program-integrity-manager@benefits.windrose | Program Integrity Manager |
| program-audit-lead@benefits.windrose | Program Audit Lead |

## Windrose Utility Inspections  (`wr-utility`)
- tenant id: `019f6a76-a5cc-76ed-8ca8-16f06be4e3ce`
- workspace: `019f6a76-aa06-7651-a05d-9fb19f6f1d05`
- packs: utility-inspections

| login email | role |
|---|---|
| admin@utility.windrose | Tenant Admin (author) |
| approver@utility.windrose | Tenant Admin (four-eyes approver) |
| inspection-triage-analyst@utility.windrose | Inspection Triage Analyst |
| field-verification-engineer@utility.windrose | Field Verification Engineer |
| vegetation-program-specialist@utility.windrose | Vegetation Program Specialist |
| asset-risk-manager@utility.windrose | Asset Risk Manager |
| regulatory-compliance-auditor@utility.windrose | Regulatory Compliance Auditor |

## Windrose Construction Claims  (`wr-construction`)
- tenant id: `019f6a76-f8ae-724d-bca7-44f2931b869d`
- workspace: `019f6a76-fcdc-7cc9-ae30-842dcc2fa42d`
- packs: construction-claims

| login email | role |
|---|---|
| admin@construction.windrose | Tenant Admin (author) |
| approver@construction.windrose | Tenant Admin (four-eyes approver) |
| claims-analyst@construction.windrose | Claims Analyst |
| scheduling-delay-specialist@construction.windrose | Scheduling & Delay Specialist |
| contract-administrator@construction.windrose | Contract Administrator |
| claims-review-board-manager@construction.windrose | Claims Review Board Manager |
| project-controls-auditor@construction.windrose | Project Controls Auditor |

## Windrose AP Audit  (`wr-apaudit`)
- tenant id: `019f6a77-4bc9-7b8d-a363-570dc178db9d`
- workspace: `019f6a77-5015-74f1-a0b6-a923a85392e4`
- packs: ap-invoice-audit

| login email | role |
|---|---|
| admin@apaudit.windrose | Tenant Admin (author) |
| approver@apaudit.windrose | Tenant Admin (four-eyes approver) |
| ap-exception-analyst@apaudit.windrose | AP Exception Analyst |
| recovery-audit-analyst@apaudit.windrose | Recovery Audit Analyst |
| vendor-master-specialist@apaudit.windrose | Vendor Master Specialist |
| ap-controls-manager@apaudit.windrose | AP Controls Manager |
| internal-controls-auditor@apaudit.windrose | Internal Controls Auditor |

## Windrose Manufacturing Quality  (`wr-mrb`)
- tenant id: `019f6a77-9eba-71a1-a490-b8a9a58863bb`
- workspace: `019f6a77-a307-7576-916e-0b46e3c7cf11`
- packs: manufacturing-mrb

| login email | role |
|---|---|
| admin@mrb.windrose | Tenant Admin (author) |
| approver@mrb.windrose | Tenant Admin (four-eyes approver) |
| quality-engineer@mrb.windrose | Quality Engineer |
| mrb-engineering-reviewer@mrb.windrose | MRB Engineering Reviewer |
| supplier-quality-engineer@mrb.windrose | Supplier Quality Engineer |
| quality-manager@mrb.windrose | Quality Manager |
| quality-systems-auditor@mrb.windrose | Quality Systems Auditor |

## Windrose Tax Notices  (`wr-tax`)
- tenant id: `019f6a77-f193-75c8-8af7-e27aa7bc5024`
- workspace: `019f6a77-f5ca-74f5-903a-3c4d99408ce3`
- packs: tax-notices

| login email | role |
|---|---|
| admin@tax.windrose | Tenant Admin (author) |
| approver@tax.windrose | Tenant Admin (four-eyes approver) |
| tax-notice-analyst@tax.windrose | Tax Notice Analyst |
| controversy-abatement-lead@tax.windrose | Controversy & Abatement Lead |
| sales-tax-specialist@tax.windrose | Sales Tax Specialist |
| tax-compliance-manager@tax.windrose | Tax Compliance Manager |
| tax-governance-auditor@tax.windrose | Tax Governance Auditor |

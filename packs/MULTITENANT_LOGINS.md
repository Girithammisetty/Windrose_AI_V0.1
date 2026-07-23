# Multi-tenant pack logins (manual testing)

Dev login at http://localhost:3000/login — enter the email, any password.
Each tenant is a fully isolated (RLS) vertical with its own users.

## Datacern Payer Claims Co  (`wr-payer`)
- tenant id: `019f6460-7827-7724-8d90-db30d401436d`
- workspace: `019f6460-7c59-7979-8097-04b58e68a9fb`
- packs: insurance-claims-payer

| login email | role |
|---|---|
| admin@payer.datacern | Tenant Admin (author) |
| approver@payer.datacern | Tenant Admin (four-eyes approver) |
| pa-nurse-reviewer@payer.datacern | PA Nurse Reviewer |
| medical-director@payer.datacern | Medical Director |
| appeals-analyst@payer.datacern | Appeals Analyst |
| payment-integrity-analyst@payer.datacern | Payment Integrity Analyst |
| compliance-officer@payer.datacern | Compliance Officer |
| member-services@payer.datacern | Member Services |

## Datacern Care Management  (`wr-caremgmt`)
- tenant id: `019f6461-9373-7f2a-a671-65e33d71affa`
- workspace: `019f6461-97a8-73b1-9dd4-2f8ac484d19d`
- packs: care-management-medicare

| login email | role |
|---|---|
| admin@caremgmt.datacern | Tenant Admin (author) |
| approver@caremgmt.datacern | Tenant Admin (four-eyes approver) |
| care-manager-rn@caremgmt.datacern | Care Manager RN |
| care-manager-lpn@caremgmt.datacern | Care Manager LPN |
| clinician-md-reviewer@caremgmt.datacern | Clinician MD Reviewer |
| director-of-care-coordination@caremgmt.datacern | Director of Care Coordination |
| practice-cfo@caremgmt.datacern | Practice CFO |
| compliance-officer@caremgmt.datacern | Compliance Officer |

## Datacern Provider RCM  (`wr-rcm`)
- tenant id: `019f6461-bb9c-779d-b209-36c68926d6d2`
- workspace: `019f6461-bfd8-7bcb-8905-2086370efe10`
- packs: healthcare-provider-rcm

| login email | role |
|---|---|
| admin@rcm.datacern | Tenant Admin (author) |
| approver@rcm.datacern | Tenant Admin (four-eyes approver) |
| biller@rcm.datacern | Biller |
| medical-coder@rcm.datacern | Medical Coder |
| denials-specialist@rcm.datacern | Denials Specialist |
| a-r-manager@rcm.datacern | A/R Manager |
| revenue-integrity-analyst@rcm.datacern | Revenue Integrity Analyst |
| revenue-cycle-director@rcm.datacern | Revenue Cycle Director |

## Datacern Payer FWA-SIU  (`wr-fwa`)
- tenant id: `019f6461-e3f8-75c4-b773-9123836c8033`
- workspace: `019f6461-e821-7cd9-ac57-d34ea4b8751d`
- packs: investigation-framework, payer-fwa-siu

| login email | role |
|---|---|
| admin@fwa.datacern | Tenant Admin (author) |
| approver@fwa.datacern | Tenant Admin (four-eyes approver) |
| investigator@fwa.datacern | Investigator |
| investigation-supervisor@fwa.datacern | Investigation Supervisor |
| evidence-custodian@fwa.datacern | Evidence Custodian |
| investigation-quality-reviewer@fwa.datacern | Investigation Quality Reviewer |
| siu-investigator@fwa.datacern | SIU Investigator |
| siu-supervisor@fwa.datacern | SIU Supervisor |
| siu-director@fwa.datacern | SIU Director |
| payment-integrity-analyst@fwa.datacern | Payment Integrity Analyst |
| compliance-auditor@fwa.datacern | Compliance Auditor |

## Datacern Pharmacy Benefits  (`wr-pbm`)
- tenant id: `019f6462-14dc-7249-8e1f-faa5e562b126`
- workspace: `019f6462-1925-7d0f-8046-a3694911b658`
- packs: pharmacy-benefit-mgmt

| login email | role |
|---|---|
| admin@pbm.datacern | Tenant Admin (author) |
| approver@pbm.datacern | Tenant Admin (four-eyes approver) |
| pa-pharmacist@pbm.datacern | PA Pharmacist |
| formulary-manager@pbm.datacern | Formulary Manager |
| rebate-analyst@pbm.datacern | Rebate Analyst |
| clinical-director@pbm.datacern | Clinical Director |
| pbm-compliance-officer@pbm.datacern | PBM Compliance Officer |

## Datacern Post-Acute Care  (`wr-pac`)
- tenant id: `019f6462-43af-7c19-83f8-feb3d4eee63e`
- workspace: `019f6462-47e5-79db-b828-8871ff03bcb0`
- packs: post-acute-care

| login email | role |
|---|---|
| admin@pac.datacern | Tenant Admin (author) |
| approver@pac.datacern | Tenant Admin (four-eyes approver) |
| hha-clinical-nurse@pac.datacern | HHA Clinical Nurse |
| snf-mds-coordinator@pac.datacern | SNF MDS Coordinator |
| intake-coordinator@pac.datacern | Intake Coordinator |
| post-acute-care-manager@pac.datacern | Post-Acute Care Manager |
| pac-compliance-officer@pac.datacern | PAC Compliance Officer |

## Datacern Banking AML  (`wr-aml`)
- tenant id: `019f6462-6b63-7a87-a307-5fda2e2e2ac1`
- workspace: `019f6462-7388-7b52-a1bd-882b060c29cd`
- packs: investigation-framework, banking-aml

| login email | role |
|---|---|
| admin@aml.datacern | Tenant Admin (author) |
| approver@aml.datacern | Tenant Admin (four-eyes approver) |
| investigator@aml.datacern | Investigator |
| investigation-supervisor@aml.datacern | Investigation Supervisor |
| evidence-custodian@aml.datacern | Evidence Custodian |
| investigation-quality-reviewer@aml.datacern | Investigation Quality Reviewer |
| aml-analyst-l1@aml.datacern | AML Analyst L1 |
| aml-investigator-l2@aml.datacern | AML Investigator L2 |
| mlro@aml.datacern | MLRO |
| sanctions-analyst@aml.datacern | Sanctions Analyst |
| model-risk-validator@aml.datacern | Model Risk Validator |

## Datacern Card Disputes  (`wr-disputes`)
- tenant id: `019f6a51-9089-7acd-a7ae-db015c85d0c0`
- workspace: `019f6a51-94ea-717a-912e-8c21357a50b7`
- packs: card-disputes

| login email | role |
|---|---|
| admin@disputes.datacern | Tenant Admin (author) |
| approver@disputes.datacern | Tenant Admin (four-eyes approver) |
| dispute-intake-analyst@disputes.datacern | Dispute Intake Analyst |
| fraud-investigator@disputes.datacern | Fraud Investigator |
| chargeback-specialist@disputes.datacern | Chargeback Specialist |
| dispute-operations-manager@disputes.datacern | Dispute Operations Manager |
| dispute-compliance-auditor@disputes.datacern | Dispute Compliance Auditor |

## Datacern Pharmacovigilance  (`wr-pv`)
- tenant id: `019f6a51-e8f1-790c-97a4-28c234c5e652`
- workspace: `019f6a51-ed51-77c8-a331-a7ddd5f1b751`
- packs: pharmacovigilance

| login email | role |
|---|---|
| admin@pv.datacern | Tenant Admin (author) |
| approver@pv.datacern | Tenant Admin (four-eyes approver) |
| pv-intake-specialist@pv.datacern | PV Intake Specialist |
| pv-medical-reviewer@pv.datacern | PV Medical Reviewer |
| pv-safety-officer@pv.datacern | PV Safety Officer |
| pv-signal-analyst@pv.datacern | PV Signal Analyst |
| pv-quality-auditor@pv.datacern | PV Quality Auditor |

## Datacern Workers Comp  (`wr-wcomp`)
- tenant id: `019f6a73-b6ea-7d88-ae23-8da115f46580`
- workspace: `019f6a73-bb44-79bc-bd20-2a166dba063e`
- packs: workers-comp-claims

| login email | role |
|---|---|
| admin@wcomp.datacern | Tenant Admin (author) |
| approver@wcomp.datacern | Tenant Admin (four-eyes approver) |
| wc-claims-adjuster@wcomp.datacern | WC Claims Adjuster |
| wc-nurse-case-manager@wcomp.datacern | WC Nurse Case Manager |
| wc-medical-bill-reviewer@wcomp.datacern | WC Medical Bill Reviewer |
| wc-claims-manager@wcomp.datacern | WC Claims Manager |
| wc-compliance-auditor@wcomp.datacern | WC Compliance Auditor |

## Datacern Trucking Claims  (`wr-trucking`)
- tenant id: `019f6a74-0870-7937-8d9a-8d43fca3b661`
- workspace: `019f6a74-0cb8-72f5-b6d9-3def85626d0f`
- packs: trucking-claims

| login email | role |
|---|---|
| admin@trucking.datacern | Tenant Admin (author) |
| approver@trucking.datacern | Tenant Admin (four-eyes approver) |
| claims-analyst@trucking.datacern | Claims Analyst |
| carrier-compliance-analyst@trucking.datacern | Carrier Compliance Analyst |
| safety-review-specialist@trucking.datacern | Safety Review Specialist |
| claims-safety-manager@trucking.datacern | Claims & Safety Manager |
| fleet-compliance-auditor@trucking.datacern | Fleet Compliance Auditor |

## Datacern Warranty Claims  (`wr-warranty`)
- tenant id: `019f6a74-32cc-7700-b9bf-d102bf802e29`
- workspace: `019f6a74-3720-7766-98eb-ec1beef2974c`
- packs: warranty-claims

| login email | role |
|---|---|
| admin@warranty.datacern | Tenant Admin (author) |
| approver@warranty.datacern | Tenant Admin (four-eyes approver) |
| warranty-claims-analyst@warranty.datacern | Warranty Claims Analyst |
| technical-assessor@warranty.datacern | Technical Assessor |
| supplier-recovery-specialist@warranty.datacern | Supplier Recovery Specialist |
| warranty-operations-manager@warranty.datacern | Warranty Operations Manager |
| warranty-audit-lead@warranty.datacern | Warranty Audit Lead |

## Datacern Loss Mitigation  (`wr-lossmit`)
- tenant id: `019f6a74-6849-7728-9207-35be40748365`
- workspace: `019f6a74-6c80-7e77-a754-362f18fb335b`
- packs: mortgage-loss-mitigation

| login email | role |
|---|---|
| admin@lossmit.datacern | Tenant Admin (author) |
| approver@lossmit.datacern | Tenant Admin (four-eyes approver) |
| loss-mitigation-specialist@lossmit.datacern | Loss Mitigation Specialist |
| underwriting-reviewer@lossmit.datacern | Underwriting Reviewer |
| spoc-coordinator@lossmit.datacern | SPOC Coordinator |
| loss-mitigation-manager@lossmit.datacern | Loss Mitigation Manager |
| servicing-compliance-auditor@lossmit.datacern | Servicing Compliance Auditor |

## Datacern Credit Disputes  (`wr-fcra`)
- tenant id: `019f6a74-98d8-787b-a76d-6bac9ef7c30f`
- workspace: `019f6a74-9943-7a0e-aff9-ae0e076fcdb0`
- packs: credit-disputes

| login email | role |
|---|---|
| admin@fcra.datacern | Tenant Admin (author) |
| approver@fcra.datacern | Tenant Admin (four-eyes approver) |
| dispute-investigator@fcra.datacern | Dispute Investigator |
| furnisher-data-analyst@fcra.datacern | Furnisher Data Analyst |
| identity-theft-specialist@fcra.datacern | Identity Theft Specialist |
| dispute-operations-manager@fcra.datacern | Dispute Operations Manager |
| fcra-compliance-auditor@fcra.datacern | FCRA Compliance Auditor |

## Datacern Background Screening  (`wr-screening`)
- tenant id: `019f6a74-c5c0-71ad-be3d-a6e11f590680`
- workspace: `019f6a74-c9f7-7b89-9147-d2549697f353`
- packs: background-screening

| login email | role |
|---|---|
| admin@screening.datacern | Tenant Admin (author) |
| approver@screening.datacern | Tenant Admin (four-eyes approver) |
| screening-adjudicator@screening.datacern | Screening Adjudicator |
| identity-resolution-specialist@screening.datacern | Identity Resolution Specialist |
| adverse-action-coordinator@screening.datacern | Adverse Action Coordinator |
| screening-operations-manager@screening.datacern | Screening Operations Manager |
| fcra-compliance-auditor@screening.datacern | FCRA Compliance Auditor |

## Datacern Trade Compliance  (`wr-trade`)
- tenant id: `019f6a73-e0af-7d6d-b381-1a26863a6a2e`
- workspace: `019f6a73-e0f9-7b4e-9c9a-a7b5404d9446`
- packs: trade-compliance

| login email | role |
|---|---|
| admin@trade.datacern | Tenant Admin (author) |
| approver@trade.datacern | Tenant Admin (four-eyes approver) |
| classification-analyst@trade.datacern | Classification Analyst |
| screening-analyst@trade.datacern | Screening Analyst |
| licensing-specialist@trade.datacern | Licensing Specialist |
| trade-compliance-manager@trade.datacern | Trade Compliance Manager |
| trade-audit-lead@trade.datacern | Trade Audit Lead |

## Datacern Trust & Safety  (`wr-appeals`)
- tenant id: `019f6a74-f27f-72f1-9489-932d4d188e80`
- workspace: `019f6a74-f6ce-7b1b-8194-07c91989d7dd`
- packs: trust-safety-appeals

| login email | role |
|---|---|
| admin@appeals.datacern | Tenant Admin (author) |
| approver@appeals.datacern | Tenant Admin (four-eyes approver) |
| appeals-reviewer@appeals.datacern | Appeals Reviewer |
| senior-policy-reviewer@appeals.datacern | Senior Policy Reviewer |
| escalations-specialist@appeals.datacern | Escalations Specialist |
| appeals-operations-manager@appeals.datacern | Appeals Operations Manager |
| transparency-audit-lead@appeals.datacern | Transparency & Audit Lead |

## Datacern Device Vigilance  (`wr-mdr`)
- tenant id: `019f6a75-0a20-7e92-8c4f-36c3f4f88c44`
- workspace: `019f6a75-0e71-75d0-892f-52b82da9d36a`
- packs: device-complaints

| login email | role |
|---|---|
| admin@mdr.datacern | Tenant Admin (author) |
| approver@mdr.datacern | Tenant Admin (four-eyes approver) |
| complaint-intake-coordinator@mdr.datacern | Complaint Intake Coordinator |
| complaint-investigator@mdr.datacern | Complaint Investigator |
| mdr-reportability-analyst@mdr.datacern | MDR Reportability Analyst |
| quality-regulatory-manager@mdr.datacern | Quality & Regulatory Manager |
| quality-systems-auditor@mdr.datacern | Quality Systems Auditor |

## Datacern Underwriting Intake  (`wr-uw`)
- tenant id: `019f6a75-5d07-7d56-b83a-fc2b75598d32`
- workspace: `019f6a75-613f-71a5-9296-8769e73f14c6`
- packs: underwriting-intake

| login email | role |
|---|---|
| admin@uw.datacern | Tenant Admin (author) |
| approver@uw.datacern | Tenant Admin (four-eyes approver) |
| submission-intake-analyst@uw.datacern | Submission Intake Analyst |
| appetite-clearance-specialist@uw.datacern | Appetite & Clearance Specialist |
| underwriting-assistant@uw.datacern | Underwriting Assistant |
| underwriting-operations-manager@uw.datacern | Underwriting Operations Manager |
| underwriting-audit-lead@uw.datacern | Underwriting Audit Lead |

## Datacern Merchant Disputes  (`wr-merchant`)
- tenant id: `019f6a75-b029-79f6-bfe9-337494ef122a`
- workspace: `019f6a75-b487-7a9e-9bb9-bbabac27e7ed`
- packs: chargeback-representment

| login email | role |
|---|---|
| admin@merchant.datacern | Tenant Admin (author) |
| approver@merchant.datacern | Tenant Admin (four-eyes approver) |
| dispute-response-analyst@merchant.datacern | Dispute Response Analyst |
| evidence-specialist@merchant.datacern | Evidence Specialist |
| pre-arbitration-lead@merchant.datacern | Pre-Arbitration Lead |
| dispute-program-manager@merchant.datacern | Dispute Program Manager |
| payments-compliance-auditor@merchant.datacern | Payments Compliance Auditor |

## Datacern Marketplace Integrity  (`wr-marketplace`)
- tenant id: `019f6a76-03c6-7c8e-9ba5-b6d939688877`
- workspace: `019f6a76-041c-73f3-9e60-08df10523dc0`
- packs: seller-vetting

| login email | role |
|---|---|
| admin@marketplace.datacern | Tenant Admin (author) |
| approver@marketplace.datacern | Tenant Admin (four-eyes approver) |
| vetting-analyst@marketplace.datacern | Vetting Analyst |
| marketplace-integrity-investigator@marketplace.datacern | Marketplace Integrity Investigator |
| ip-claims-reviewer@marketplace.datacern | IP Claims Reviewer |
| marketplace-trust-manager@marketplace.datacern | Marketplace Trust Manager |
| marketplace-compliance-auditor@marketplace.datacern | Marketplace Compliance Auditor |

## Datacern Benefits Adjudication  (`wr-benefits`)
- tenant id: `019f6a76-530d-7301-a968-51a719fb802d`
- workspace: `019f6a76-5759-78c2-b225-860d97a63d6a`
- packs: benefits-appeals

| login email | role |
|---|---|
| admin@benefits.datacern | Tenant Admin (author) |
| approver@benefits.datacern | Tenant Admin (four-eyes approver) |
| eligibility-examiner@benefits.datacern | Eligibility Examiner |
| appeals-hearing-preparer@benefits.datacern | Appeals Hearing Preparer |
| overpayment-analyst@benefits.datacern | Overpayment Analyst |
| program-integrity-manager@benefits.datacern | Program Integrity Manager |
| program-audit-lead@benefits.datacern | Program Audit Lead |

## Datacern Utility Inspections  (`wr-utility`)
- tenant id: `019f6a76-a5cc-76ed-8ca8-16f06be4e3ce`
- workspace: `019f6a76-aa06-7651-a05d-9fb19f6f1d05`
- packs: utility-inspections

| login email | role |
|---|---|
| admin@utility.datacern | Tenant Admin (author) |
| approver@utility.datacern | Tenant Admin (four-eyes approver) |
| inspection-triage-analyst@utility.datacern | Inspection Triage Analyst |
| field-verification-engineer@utility.datacern | Field Verification Engineer |
| vegetation-program-specialist@utility.datacern | Vegetation Program Specialist |
| asset-risk-manager@utility.datacern | Asset Risk Manager |
| regulatory-compliance-auditor@utility.datacern | Regulatory Compliance Auditor |

## Datacern Construction Claims  (`wr-construction`)
- tenant id: `019f6a76-f8ae-724d-bca7-44f2931b869d`
- workspace: `019f6a76-fcdc-7cc9-ae30-842dcc2fa42d`
- packs: construction-claims

| login email | role |
|---|---|
| admin@construction.datacern | Tenant Admin (author) |
| approver@construction.datacern | Tenant Admin (four-eyes approver) |
| claims-analyst@construction.datacern | Claims Analyst |
| scheduling-delay-specialist@construction.datacern | Scheduling & Delay Specialist |
| contract-administrator@construction.datacern | Contract Administrator |
| claims-review-board-manager@construction.datacern | Claims Review Board Manager |
| project-controls-auditor@construction.datacern | Project Controls Auditor |

## Datacern AP Audit  (`wr-apaudit`)
- tenant id: `019f6a77-4bc9-7b8d-a363-570dc178db9d`
- workspace: `019f6a77-5015-74f1-a0b6-a923a85392e4`
- packs: ap-invoice-audit

| login email | role |
|---|---|
| admin@apaudit.datacern | Tenant Admin (author) |
| approver@apaudit.datacern | Tenant Admin (four-eyes approver) |
| ap-exception-analyst@apaudit.datacern | AP Exception Analyst |
| recovery-audit-analyst@apaudit.datacern | Recovery Audit Analyst |
| vendor-master-specialist@apaudit.datacern | Vendor Master Specialist |
| ap-controls-manager@apaudit.datacern | AP Controls Manager |
| internal-controls-auditor@apaudit.datacern | Internal Controls Auditor |

## Datacern Manufacturing Quality  (`wr-mrb`)
- tenant id: `019f6a77-9eba-71a1-a490-b8a9a58863bb`
- workspace: `019f6a77-a307-7576-916e-0b46e3c7cf11`
- packs: manufacturing-mrb

| login email | role |
|---|---|
| admin@mrb.datacern | Tenant Admin (author) |
| approver@mrb.datacern | Tenant Admin (four-eyes approver) |
| quality-engineer@mrb.datacern | Quality Engineer |
| mrb-engineering-reviewer@mrb.datacern | MRB Engineering Reviewer |
| supplier-quality-engineer@mrb.datacern | Supplier Quality Engineer |
| quality-manager@mrb.datacern | Quality Manager |
| quality-systems-auditor@mrb.datacern | Quality Systems Auditor |

## Datacern Tax Notices  (`wr-tax`)
- tenant id: `019f6a77-f193-75c8-8af7-e27aa7bc5024`
- workspace: `019f6a77-f5ca-74f5-903a-3c4d99408ce3`
- packs: tax-notices

| login email | role |
|---|---|
| admin@tax.datacern | Tenant Admin (author) |
| approver@tax.datacern | Tenant Admin (four-eyes approver) |
| tax-notice-analyst@tax.datacern | Tax Notice Analyst |
| controversy-abatement-lead@tax.datacern | Controversy & Abatement Lead |
| sales-tax-specialist@tax.datacern | Sales Tax Specialist |
| tax-compliance-manager@tax.datacern | Tax Compliance Manager |
| tax-governance-auditor@tax.datacern | Tax Governance Auditor |

## Banking Aml Demo  (`wr-demo-banking-aml`)
- tenant id: `019f8221-6499-734f-9a03-087a5be057f5`
- workspace: `019f8221-68df-7ef4-8a32-99b3ec0fe5a5`
- packs: investigation-framework, banking-aml

| login email | role |
|---|---|
| admin@bankingaml.datacern | Tenant Admin (author) |
| approver@bankingaml.datacern | Tenant Admin (four-eyes approver) |
| investigator@bankingaml.datacern | Investigator |
| investigation-supervisor@bankingaml.datacern | Investigation Supervisor |
| evidence-custodian@bankingaml.datacern | Evidence Custodian |
| investigation-quality-reviewer@bankingaml.datacern | Investigation Quality Reviewer |
| aml-analyst-l1@bankingaml.datacern | AML Analyst L1 |
| aml-investigator-l2@bankingaml.datacern | AML Investigator L2 |
| mlro@bankingaml.datacern | MLRO |
| sanctions-analyst@bankingaml.datacern | Sanctions Analyst |
| model-risk-validator@bankingaml.datacern | Model Risk Validator |

## Depth Verify Card Ops  (`depth-verify`)
- tenant id: `019f90e7-c5b6-7dea-b842-0c2207e6a0e3`
- workspace: `019f90e7-c9fa-76e5-8ba7-a232d6780bc1`
- packs: card-disputes

| login email | role |
|---|---|
| admin@verify.datacern | Tenant Admin (author) |
| approver@verify.datacern | Tenant Admin (four-eyes approver) |
| dispute-intake-analyst@verify.datacern | Dispute Intake Analyst |
| fraud-investigator@verify.datacern | Fraud Investigator |
| chargeback-specialist@verify.datacern | Chargeback Specialist |
| dispute-operations-manager@verify.datacern | Dispute Operations Manager |
| dispute-compliance-auditor@verify.datacern | Dispute Compliance Auditor |

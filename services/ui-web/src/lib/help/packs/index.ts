/**
 * Registry of authored pack overlays, keyed by pack manifest name
 * (matches PackInstall.pack). card-disputes is the hand-authored exemplar;
 * the rest are grounded overlays generated from each pack.yaml + roles.yaml.
 * A tenant whose pack has no overlay still gets the full shared platform +
 * admin guides (graceful fallback in the registry).
 */
import type { PackGuide } from "../types";
import { apInvoiceAuditGuide } from "./ap-invoice-audit";
import { backgroundScreeningGuide } from "./background-screening";
import { bankingAmlGuide } from "./banking-aml";
import { benefitsAppealsGuide } from "./benefits-appeals";
import { cardDisputesGuide } from "./card-disputes";
import { careManagementMedicareGuide } from "./care-management-medicare";
import { chargebackRepresentmentGuide } from "./chargeback-representment";
import { constructionClaimsGuide } from "./construction-claims";
import { creditDisputesGuide } from "./credit-disputes";
import { deviceComplaintsGuide } from "./device-complaints";
import { healthcareProviderRcmGuide } from "./healthcare-provider-rcm";
import { insuranceClaimsPayerGuide } from "./insurance-claims-payer";
import { manufacturingMrbGuide } from "./manufacturing-mrb";
import { mortgageLossMitigationGuide } from "./mortgage-loss-mitigation";
import { payerFwaSiuGuide } from "./payer-fwa-siu";
import { pharmacovigilanceGuide } from "./pharmacovigilance";
import { pharmacyBenefitMgmtGuide } from "./pharmacy-benefit-mgmt";
import { postAcuteCareGuide } from "./post-acute-care";
import { sellerVettingGuide } from "./seller-vetting";
import { taxNoticesGuide } from "./tax-notices";
import { tradeComplianceGuide } from "./trade-compliance";
import { truckingClaimsGuide } from "./trucking-claims";
import { trustSafetyAppealsGuide } from "./trust-safety-appeals";
import { underwritingIntakeGuide } from "./underwriting-intake";
import { utilityInspectionsGuide } from "./utility-inspections";
import { warrantyClaimsGuide } from "./warranty-claims";
import { workersCompClaimsGuide } from "./workers-comp-claims";

export const PACK_GUIDES: Record<string, PackGuide> = {
  "ap-invoice-audit": apInvoiceAuditGuide,
  "background-screening": backgroundScreeningGuide,
  "banking-aml": bankingAmlGuide,
  "benefits-appeals": benefitsAppealsGuide,
  "card-disputes": cardDisputesGuide,
  "care-management-medicare": careManagementMedicareGuide,
  "chargeback-representment": chargebackRepresentmentGuide,
  "construction-claims": constructionClaimsGuide,
  "credit-disputes": creditDisputesGuide,
  "device-complaints": deviceComplaintsGuide,
  "healthcare-provider-rcm": healthcareProviderRcmGuide,
  "insurance-claims-payer": insuranceClaimsPayerGuide,
  "manufacturing-mrb": manufacturingMrbGuide,
  "mortgage-loss-mitigation": mortgageLossMitigationGuide,
  "payer-fwa-siu": payerFwaSiuGuide,
  "pharmacovigilance": pharmacovigilanceGuide,
  "pharmacy-benefit-mgmt": pharmacyBenefitMgmtGuide,
  "post-acute-care": postAcuteCareGuide,
  "seller-vetting": sellerVettingGuide,
  "tax-notices": taxNoticesGuide,
  "trade-compliance": tradeComplianceGuide,
  "trucking-claims": truckingClaimsGuide,
  "trust-safety-appeals": trustSafetyAppealsGuide,
  "underwriting-intake": underwritingIntakeGuide,
  "utility-inspections": utilityInspectionsGuide,
  "warranty-claims": warrantyClaimsGuide,
  "workers-comp-claims": workersCompClaimsGuide,
};

/** Shared *library* packs (not a tenant's headline vertical) — excluded when
 * picking the primary pack for the help home. */
export const LIBRARY_PACKS = new Set<string>(["investigation-framework"]);

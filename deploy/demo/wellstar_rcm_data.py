"""Synthetic RCM demo data — Wellstar prospect demo (healthcare-provider-rcm).

Generates the four CSVs matching the pack's dataset BINDING CONTRACTS exactly
(packs/healthcare-provider-rcm/data/datasets.yaml). This is TENANT-UPLOADED
demo fixture data, not pack content (the no-dummy-data rule governs packs);
every entity is fictional (payers, NPIs, ids) and the rows are deterministic
so the demo is reproducible.

The rows are deliberately patterned so every decision-table rule has a live
demo moment:
  * DN-3001 (the HERO): $18,450 oncology auth denial, appeal window nearly
    closed -> denial_triage rule 1 fires CRITICAL (expedite).
  * DN-3002: $9,800 administrative denial, window open -> appeal prep (HIGH).
  * DN-3003: documentation defect -> corrected resubmission (MEDIUM).
  * DN-3006: coverage (LCD/NCD) denial -> escalates to CLINICAL review — the
    table never makes the clinical call (the physician beat).
  * DN-3004 / DN-3005: small-balance + bundling -> small_balance_review.
  * 6 overturned appeals (~$43k recovered) + upheld/pending history so the
    dashboards show a real recovered-vs-written-off story.
  * DN-39xx: spare hero clones so each rehearsal uses a fresh open case.

Usage: python wellstar_rcm_data.py [outdir]   (default: ./wellstar-rcm/)
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

random.seed(42)  # deterministic

PAYERS = [
    ("Peachtree Commercial Health", "commercial"),
    ("BlueRidge BCBS of Georgia", "commercial"),
    ("Everline Medicare Advantage", "medicare_advantage"),
    ("Georgia Medicaid CMO", "medicaid"),
    ("SummitCare HMO", "managed_care"),
]
LINES = ["oncology", "cardiology", "orthopedics", "emergency", "imaging", "general_surgery"]
CPT = {"oncology": ["96413", "J9312", "77301"], "cardiology": ["93458", "92928", "93306"],
       "orthopedics": ["27447", "29881", "22551"], "emergency": ["99285", "99284", "99291"],
       "imaging": ["70553", "74177", "71260"], "general_surgery": ["47562", "44970", "49505"]}
ICD = ["C50.911", "I25.10", "M17.11", "R07.9", "K80.20", "S72.001A", "J18.9", "N39.0"]
MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]
NPIS = ["1740283651", "1932406785", "1568220943", "1477392016", "1609834527"]


def money(v: float) -> str:
    return f"{v:.2f}"


def build_claims() -> list[list]:
    rows = []
    for i in range(1, 49):
        cid = f"CLM-{2100 + i}"
        line = LINES[i % len(LINES)]
        payer, ptype = PAYERS[i % len(PAYERS)]
        billed = round(random.uniform(350, 42000), 2)
        contractual = round(billed * random.uniform(0.25, 0.55), 2)
        net = round(billed - contractual, 2)
        expected = round(net * random.uniform(0.9, 1.0), 2)
        status = ["paid", "paid", "paid", "denied", "pending", "partially_paid"][i % 6]
        paid = round(expected if status == "paid" else (expected * 0.6 if status == "partially_paid" else 0.0), 2)
        rows.append([
            cid, f"ENC-{7000 + i}", f"PAT-{5000 + (i % 30)}", payer, ptype,
            NPIS[i % len(NPIS)], line, CPT[line][i % 3], ICD[i % len(ICD)],
            MONTHS[i % len(MONTHS)], money(billed), money(contractual), money(net),
            money(expected), money(paid),
            money(round(random.uniform(0, 400), 2)), status,
            "yes" if i % 4 else "no", "yes" if i % 3 else "no", str(random.randint(12, 95)),
        ])
    return rows


def build_remits(claims: list[list]) -> list[list]:
    rows = []
    for i, c in enumerate(claims[:30], start=1):
        billed, expected, paid = float(c[10]), float(c[13]), float(c[14])
        allowed = round(expected * random.uniform(0.95, 1.0), 2)
        under = "yes" if (paid > 0 and (expected - paid) > 500) else "no"
        rows.append([
            f"RMT-{4400 + i}", c[0], c[3], MONTHS[(i + 1) % len(MONTHS)],
            ["CO-45", "CO-197", "CO-50", "CO-16", "OA-23"][i % 5],
            ["N386", "M127", "N290", "", "N4"][i % 5],
            money(billed), money(allowed), money(paid), money(expected),
            money(round(expected - paid, 2)), c[11], c[15], under,
        ])
    return rows


# (denial_id, claim_id, carc, rarc, reason_text, category, denied, appeal_status, deadline_days)
DENIAL_SPEC = [
    # ---- the demo herolines (open worklist) ----
    ("DN-3001", "CLM-2118", "CO-197", "N386",
     "Precertification/authorization absent for chemotherapy administration",
     "administrative", 18450.00, "not_appealed", 12),
    ("DN-3002", "CLM-2120", "CO-197", "M127",
     "Authorization number invalid for total knee arthroplasty",
     "administrative", 9800.00, "not_appealed", 48),
    ("DN-3003", "CLM-2119", "CO-16", "N290",
     "Claim lacks required documentation - operative note missing",
     "documentation", 2300.00, "not_appealed", 61),
    ("DN-3004", "CLM-2125", "CO-45", "",
     "Charge exceeds fee schedule - residual balance",
     "eligibility", 85.00, "not_appealed", 55),
    ("DN-3005", "CLM-2131", "CO-97", "N4",
     "Payment bundled into primary procedure (NCCI edit)",
     "bundling", 410.00, "not_appealed", 40),
    ("DN-3006", "CLM-2137", "CO-50", "N115",
     "Service not medically necessary per LCD L34567 criteria",
     "coverage", 6200.00, "not_appealed", 52),
    # ---- history: overturned appeals (the recovered-dollars story ~$43k) ----
    ("DN-3101", "CLM-2101", "CO-197", "N386", "Auth absent - overturned with auth record",
     "administrative", 12400.00, "overturned", 0),
    ("DN-3102", "CLM-2102", "CO-16", "N290", "Missing op note - overturned on resubmission",
     "documentation", 4150.00, "overturned", 0),
    ("DN-3103", "CLM-2103", "CO-197", "M127", "Invalid auth number - overturned",
     "administrative", 8900.00, "overturned", 0),
    ("DN-3104", "CLM-2104", "CO-50", "N115", "Medical necessity - overturned after peer review",
     "coverage", 9300.00, "overturned", 0),
    ("DN-3105", "CLM-2105", "CO-16", "", "Documentation defect - overturned",
     "documentation", 3200.00, "overturned", 0),
    ("DN-3106", "CLM-2106", "CO-197", "N386", "Auth absent - overturned",
     "administrative", 5350.00, "overturned", 0),
    # ---- history: upheld + pending ----
    ("DN-3201", "CLM-2108", "CO-50", "N115", "Medical necessity - appeal upheld",
     "coverage", 7100.00, "upheld", 0),
    ("DN-3202", "CLM-2109", "CO-97", "N4", "Bundling edit - appeal upheld",
     "bundling", 480.00, "upheld", 0),
    ("DN-3203", "CLM-2110", "CO-45", "", "Fee schedule - appeal upheld",
     "eligibility", 220.00, "upheld", 0),
    ("DN-3204", "CLM-2111", "CO-16", "N290", "Documentation - appeal upheld",
     "documentation", 1900.00, "upheld", 0),
    ("DN-3301", "CLM-2112", "CO-197", "N386", "Auth absent - appeal pending with payer",
     "administrative", 11250.00, "appealed", 33),
    ("DN-3302", "CLM-2114", "CO-50", "N115", "Medical necessity - appeal pending",
     "coverage", 5600.00, "appealed", 27),
    ("DN-3303", "CLM-2115", "CO-16", "", "Documentation - appeal pending",
     "documentation", 2750.00, "appealed", 41),
    # ---- rehearsal spares (hero clones; fresh open case per run-through) ----
    ("DN-3901", "CLM-2143", "CO-197", "N386",
     "Precertification absent for cardiac catheterization",
     "administrative", 14200.00, "not_appealed", 9),
    ("DN-3902", "CLM-2144", "CO-197", "N386",
     "Precertification absent for spinal fusion",
     "administrative", 21800.00, "not_appealed", 15),
    ("DN-3903", "CLM-2140", "CO-197", "M127",
     "Authorization mismatch for imaging series",
     "administrative", 7650.00, "not_appealed", 11),
]


def build_denials(claims: list[list]) -> list[list]:
    by_id = {c[0]: c for c in claims}
    rows = []
    for spec in DENIAL_SPEC:
        did, cid, carc, rarc, text, cat, amt, status, deadline = spec
        c = by_id[cid]
        rows.append([did, cid, carc, rarc, text, cat, c[7], c[6], c[3],
                     c[9], money(amt), status, str(deadline)])
    return rows


def build_ar(claims: list[list]) -> list[list]:
    buckets = ["0_30", "31_60", "61_90", "91_120", "over_120"]
    actions = ["none", "called_payer", "appeal_filed", "statement_sent", "escalated"]
    rows = []
    for i, c in enumerate(claims[:28], start=1):
        bucket = buckets[i % 5]
        rows.append([
            f"AR-{6600 + i}", c[0], c[2], c[3], c[4], c[6], bucket,
            "yes" if bucket in ("91_120", "over_120") else "no",
            "insurance" if i % 3 else "patient",
            money(round(random.uniform(120, 16000), 2)),
            str({"0_30": 18, "31_60": 44, "61_90": 75, "91_120": 104, "over_120": 156}[bucket] + (i % 9)),
            f"{random.uniform(0.2, 0.97):.2f}", actions[i % 5], "2026-07",
        ])
    return rows


HEADERS = {
    "rcm_claims.csv": ["claim_id", "encounter_id", "patient_account_id", "payer_name",
                       "payer_type", "rendering_provider_npi", "service_line", "cpt_code",
                       "icd10_code", "service_month", "billed_amount", "contractual_adjustment",
                       "net_charges", "expected_paid", "paid_amount", "patient_responsibility",
                       "status", "clean_claim", "first_pass", "days_to_payment"],
    "rcm_remits.csv": ["remit_id", "claim_id", "payer_name", "remit_month", "carc_code",
                       "rarc_code", "billed_amount", "allowed_amount", "paid_amount",
                       "expected_paid", "payment_variance", "contractual_adjustment",
                       "patient_responsibility", "underpayment_flag"],
    "rcm_denials.csv": ["denial_id", "claim_id", "carc_code", "rarc_code", "denial_reason_text",
                        "denial_category", "cpt_code", "service_line", "payer_name",
                        "denial_month", "denied_amount", "appeal_status", "appeal_deadline_days"],
    "rcm_ar_aging.csv": ["ar_id", "claim_id", "patient_account_id", "payer_name", "payer_type",
                         "service_line", "aging_bucket", "over_90", "balance_type",
                         "balance_amount", "days_outstanding", "followup_priority_score",
                         "last_action", "as_of_month"],
}


def main(outdir: str = "wellstar-rcm") -> None:
    out = Path(__file__).parent / outdir
    out.mkdir(parents=True, exist_ok=True)
    claims = build_claims()
    data = {
        "rcm_claims.csv": claims,
        "rcm_remits.csv": build_remits(claims),
        "rcm_denials.csv": build_denials(claims),
        "rcm_ar_aging.csv": build_ar(claims),
    }
    for name, rows in data.items():
        with open(out / name, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADERS[name])
            w.writerows(rows)
        print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))

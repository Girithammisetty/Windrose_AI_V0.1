"""X12 835 remittance decode (BRD 57 inc-3a, STD-FR-011/015).

The load-bearing test here is the CORRELATION one: an 837 claim and its 835
remittance must meet on `claim_id`, because that join is what turns "we billed X"
and "they paid Y" into the underpayment BRD 26's detector proposes on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.domain.decode import DecodeStats
from app.domain.errors import PermanentJobError
from app.domain.x12 import decode_x12
from tests.unit.test_x12 import build_837, build_isa


def build_835(
    *, isa_control: str = "000000001", gs_control: str = "1", st_control: str = "0001",
    payments: list[dict] | None = None, elem: str = "*", comp: str = ":", term: str = "~",
) -> bytes:
    """A minimal 835: BPR payment header, TRN trace, PR/PE names, then CLP loops."""
    pays = payments if payments is not None else [
        {"claim_id": "CLAIM1", "status": "1", "charged": "200.00", "paid": "150.00",
         "patient": "50.00", "adj": ("CO", "45", "50.00"), "lines": 1},
        {"claim_id": "CLAIM2", "status": "1", "charged": "80.00", "paid": "80.00",
         "patient": "0.00", "adj": None, "lines": 2},
    ]
    total = sum(float(p["paid"]) for p in pays)
    segs = [
        f"GS{elem}HP{elem}SENDER{elem}RECEIVER{elem}20210101{elem}1200{elem}{gs_control}{elem}X{elem}005010X221A1",
        f"ST{elem}835{elem}{st_control}",
        # BPR: 01=I 02=amount 03=C 04=ACH ... 16=effective date. The date is
        # element 16 per the 005010 spec, so there are 11 empty elements (5..15).
        f"BPR{elem}I{elem}{total:.2f}{elem}C{elem}ACH{elem}{elem * 11}20210105",
        f"TRN{elem}1{elem}EFT123456{elem}1512345678",
        f"N1{elem}PR{elem}ACME HEALTH PLAN",
        f"N1{elem}PE{elem}BILLING CLINIC{elem}XX{elem}1234567893",
    ]
    for p in pays:
        segs.append(
            f"CLP{elem}{p['claim_id']}{elem}{p['status']}{elem}{p['charged']}{elem}{p['paid']}{elem}{p['patient']}{elem}12{elem}PAYERCTL{p['claim_id']}"
        )
        if p.get("adj"):
            g, code, amt = p["adj"]
            segs.append(f"CAS{elem}{g}{elem}{code}{elem}{amt}")
        svc = comp.join(["HC", "99213"])
        for _ in range(p.get("lines", 0)):
            segs.append(f"SVC{elem}{svc}{elem}{p['charged']}{elem}{p['paid']}")
    segs.append(f"SE{elem}{len(segs)}{elem}{st_control}")
    segs.append(f"GE{elem}1{elem}{gs_control}")
    segs.append(f"IEA{elem}1{elem}{isa_control}")
    return (build_isa(isa_control, elem, comp, term) + term.join(segs) + term).encode("latin-1")


async def _stream(data: bytes, chunk: int = 64) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


async def _rows(data: bytes) -> tuple[list, list[str]]:
    stats, rows, cols = DecodeStats(), [], []
    async for batch in decode_x12(_stream(data), 5000, stats):
        cols = batch.columns
        rows.extend(batch.rows)
    return rows, cols


# ---- 835 decode -------------------------------------------------------------

async def test_decodes_one_row_per_claim_payment():
    rows, cols = await _rows(build_835())
    assert len(rows) == 2
    r = dict(zip(cols, rows[0], strict=True))
    assert r["transaction_set"] == "835"
    assert r["claim_id"] == "CLAIM1"
    assert r["charged_amount"] == "200.00"
    assert r["paid_amount"] == "150.00"
    assert r["patient_responsibility"] == "50.00"
    assert r["claim_status_code"] == "1"
    assert r["service_line_count"] == 1
    assert r["loop_path"] == "ISA/GS/ST(835)/2100"


async def test_carries_payment_header_context_on_every_row():
    rows, cols = await _rows(build_835())
    for row in rows:
        r = dict(zip(cols, row, strict=True))
        assert r["payer_name"] == "ACME HEALTH PLAN"
        assert r["payee_name"] == "BILLING CLINIC"
        assert r["check_or_eft_trace"] == "EFT123456"
        assert r["payment_method"] == "ACH"
        assert r["payment_date"] == "20210105"


async def test_captures_claim_level_adjustments():
    rows, cols = await _rows(build_835())
    r = dict(zip(cols, rows[0], strict=True))
    assert r["adjustments"] == "CO:45:50.00"       # contractual obligation, $50 written off
    r2 = dict(zip(cols, rows[1], strict=True))
    assert r2["adjustments"] == ""                 # CLAIM2 paid in full


async def test_raw_segments_preserved_for_lineage():
    rows, cols = await _rows(build_835())
    r = dict(zip(cols, rows[0], strict=True))
    assert r["raw_segments"].startswith("CLP*CLAIM1")


# ---- THE correlation (STD-FR-015) -------------------------------------------

async def test_837_and_835_correlate_on_claim_id():
    """The join that makes remittance useful: bill vs. pay on the same claim id."""
    claim_rows, ccols = await _rows(build_837(claims=2))          # CLAIM1, CLAIM2
    remit_rows, rcols = await _rows(build_835())                   # CLAIM1, CLAIM2

    billed = {dict(zip(ccols, r, strict=True))["claim_id"]:
              dict(zip(ccols, r, strict=True))["total_charge"] for r in claim_rows}
    paid = {dict(zip(rcols, r, strict=True))["claim_id"]:
            dict(zip(rcols, r, strict=True))["paid_amount"] for r in remit_rows}

    # CLAIM1 was billed 200 (per claim index i -> 100*i) but paid 150 -> underpaid.
    assert "CLAIM1" in billed and "CLAIM1" in paid
    assert float(billed["CLAIM1"]) == 100.0   # 837 fixture charges 100*i
    assert float(paid["CLAIM1"]) == 150.0
    # The point: the same claim_id column joins the two transaction sets.
    assert set(billed) & set(paid) == {"CLAIM1", "CLAIM2"}


# ---- envelope conformance still applies to 835 ------------------------------

async def test_835_control_mismatch_refused():
    data = build_835()
    tampered = data.replace(b"IEA*1*000000001", b"IEA*1*000000099")
    with pytest.raises(PermanentJobError) as e:
        await _rows(tampered)
    assert "IEA02" in str(e.value)


async def test_835_streams_across_chunk_boundaries():
    stats, rows = DecodeStats(), []
    async for batch in decode_x12(_stream(build_835(), chunk=9), 5000, stats):
        rows.extend(batch.rows)
    assert len(rows) == 2
    assert stats.rows_ok == 2

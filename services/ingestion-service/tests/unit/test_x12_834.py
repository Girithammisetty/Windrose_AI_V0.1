"""X12 834 enrollment decode (BRD 57 inc-3f) — one row per HD coverage line."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.domain.decode import DecodeStats
from app.domain.errors import PermanentJobError
from app.domain.x12 import decode_x12
from tests.unit.test_x12 import build_isa


def build_834(*, members=None, elem="*", comp=":", term="~",
              isa_control="000000001", gs_control="1", st_control="0001") -> bytes:
    members = members if members is not None else [
        {"id": "SUB001", "last": "DOE", "first": "JANE", "maint": "021", "reason": "AI",
         "coverages": [("HLT", "PLAN GOLD", "20210101", "20211231"),
                       ("DEN", "DENTAL BASIC", "20210101", "20211231")]},
        {"id": "SUB002", "last": "SMITH", "first": "JOHN", "maint": "024", "reason": "TE",
         "coverages": [("HLT", "PLAN SILVER", "20210101", "20210630")]},
    ]
    gs = elem.join(["GS", "BE", "SENDER", "RECEIVER", "20210101", "1200", gs_control,
                    "X", "005010X220A1"])
    segs = [gs, f"ST{elem}834{elem}{st_control}"]
    for m in members:
        segs.append(f"INS{elem}Y{elem}18{elem}{m['maint']}{elem}{m['reason']}{elem}A{elem}C")
        segs.append(f"REF{elem}0F{elem}{m['id']}")
        segs.append(f"NM1{elem}IL{elem}1{elem}{m['last']}{elem}{m['first']}")
        for cov, plan, begin, end in m["coverages"]:
            segs.append(f"HD{elem}{m['maint']}{elem}{elem}{cov}{elem}{plan}")
            segs.append(f"DTP{elem}348{elem}D8{elem}{begin}")
            segs.append(f"DTP{elem}349{elem}D8{elem}{end}")
    segs.append(f"SE{elem}{len(segs)}{elem}{st_control}")
    segs.append(f"GE{elem}1{elem}{gs_control}")
    segs.append(f"IEA{elem}1{elem}{isa_control}")
    return (build_isa(isa_control, elem, comp, term) + term.join(segs) + term).encode("latin-1")


async def _stream(data: bytes, chunk: int = 64) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


async def _rows(data: bytes, chunk: int = 64) -> tuple[list, list[str], DecodeStats]:
    stats, rows, cols = DecodeStats(), [], []
    async for batch in decode_x12(_stream(data, chunk), 5000, stats):
        cols = batch.columns
        rows.extend(batch.rows)
    return rows, cols, stats


async def test_one_row_per_coverage_line():
    rows, cols, stats = await _rows(build_834())
    # member 1 has 2 coverages, member 2 has 1 -> 3 rows
    assert len(rows) == 3 and stats.rows_ok == 3
    r = dict(zip(cols, rows[0], strict=True))
    assert r["transaction_set"] == "834"
    assert r["member_id"] == "SUB001"
    assert r["member_name"] == "DOE JANE"
    assert r["maintenance_type"] == "021"        # add
    assert r["maintenance_reason"] == "AI"
    assert r["coverage_type"] == "HLT"
    assert r["plan_description"] == "PLAN GOLD"
    assert r["benefit_begin"] == "20210101"
    assert r["benefit_end"] == "20211231"
    assert r["loop_path"] == "ISA/GS/ST(834)/2300"


async def test_second_coverage_keeps_member_context():
    rows, cols, _ = await _rows(build_834())
    dental = dict(zip(cols, rows[1], strict=True))
    assert dental["member_id"] == "SUB001"       # same member
    assert dental["coverage_type"] == "DEN"
    assert dental["plan_description"] == "DENTAL BASIC"


async def test_second_member_switches_context():
    rows, cols, _ = await _rows(build_834())
    m2 = dict(zip(cols, rows[2], strict=True))
    assert m2["member_id"] == "SUB002"
    assert m2["member_name"] == "SMITH JOHN"
    assert m2["maintenance_type"] == "024"       # cancel
    assert m2["benefit_end"] == "20210630"


async def test_834_streams_and_conformance_holds():
    stats, rows = DecodeStats(), []
    async for batch in decode_x12(_stream(build_834(), chunk=9), 5000, stats):
        rows.extend(batch.rows)
    assert len(rows) == 3


async def test_834_control_mismatch_refused():
    data = build_834().replace(b"IEA*1*000000001", b"IEA*1*000000088")
    with pytest.raises(PermanentJobError) as e:
        await _rows(data)
    assert "IEA02" in str(e.value)

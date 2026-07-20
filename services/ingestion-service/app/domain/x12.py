"""X12 EDI decoding (BRD 57 STD-FR-010/011, BR-2/BR-4, AC-1/AC-4/AC-8).

The grammar layer for ASC X12 interchanges. `decode.py` stays the thin format
dispatcher; the envelope/loop machinery lives here, mirroring how the other
non-trivial decoders are structured.

Design notes that matter:

* **Delimiters are self-describing.** A conformant ISA is a fixed-width 106-char
  segment, so the element separator, component separator and segment terminator
  are READ FROM THE DATA (ISA[3], ISA[104], ISA[105]) rather than assumed to be
  ``*``/``:``/``~``. Real trading partners vary these, and hardcoding them is the
  single most common cause of "works for one payer only" EDI parsers.

* **Streaming.** Segments are tokenized off the byte stream and claim rows are
  emitted as each 2300 loop closes; a 500MB 837 never lands in memory. Matches
  the decoder contract in decode.py.

* **Refuse, never half-parse (BR-2 / Rule 2).** A malformed envelope, an
  unsupported transaction set, or a control-number mismatch raises
  PermanentJobError. Because the runner consumes this generator inside
  `table_writer.stage(...)`, a raise means no StagedAppend and therefore no
  commit — zero rows reach the dataset rather than a plausible-looking subset.

* **Fidelity (BR-4).** Every emitted row carries its interchange/group/transaction
  control numbers, its loop path, and the claim's RAW segments, so any downstream
  decision is traceable to the exact bytes received.

* **PHI (BR-5).** 837 content is PHI — member names, member ids, diagnosis codes —
  and `raw_segments` reproduces it verbatim by design. Rows land in the tenant's
  own dataset under the existing RLS wall, and the raw column is subject to the
  same PII-egress guardrails as any other governed column. Treat this decoder's
  output as regulated data, not as logs.

inc-1 supports 837 (professional/institutional) claims. 835/834/270/271/276/277
are recognised transaction sets that this build does not yet decode: they are
refused BY NAME (AC-8) instead of silently yielding nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.domain.errors import ErrorCategory, PermanentJobError

#: A conformant ISA is exactly 106 characters including its terminator.
ISA_LEN = 106
#: Transaction sets this build decodes into rows. Each has its OWN row schema
#: (a remittance is not a claim), so the envelope machinery is shared and the
#: row building is dispatched to a per-transaction-set handler.
SUPPORTED_TRANSACTION_SETS = ("837", "835", "271", "277", "834")
#: Recognised but not yet decoded — refused by name so the operator gets a real
#: reason rather than an empty dataset (BR-2/AC-8). 270/276 are the outbound
#: INQUIRY halves (we send them); the platform decodes the RESPONSES (271/277).
KNOWN_TRANSACTION_SETS = ("270", "276", "997", "999")

# --- hardening caps ---------------------------------------------------------
# X12 is attacker-reachable (a partner drops a file on our SFTP), so the parser
# is bounded the same way the XML decoder rejects DTDs and uploads cap bytes.
MAX_SEGMENT_CHARS = 100_000       # a single segment past this is corrupt/hostile
MAX_ELEMENTS_PER_SEGMENT = 2_000
MAX_SEGMENTS_PER_TRANSACTION = 5_000_000
MAX_RAW_SEGMENTS_PER_CLAIM = 2_000  # bounds the lineage column per row

#: Envelope identity carried onto EVERY row regardless of transaction set, so a
#: claim and its remittance can be correlated on control numbers + claim id.
_ENVELOPE_COLUMNS = [
    "interchange_control_number",
    "group_control_number",
    "transaction_control_number",
    "transaction_set",
    "sender_id",
    "receiver_id",
]

#: 837 — one row per claim (2300 loop).
CLAIM_COLUMNS: list[str] = [
    *_ENVELOPE_COLUMNS,
    "claim_id",
    "total_charge",
    "place_of_service",
    "billing_provider_npi",
    "subscriber_id",
    "diagnosis_codes",
    "service_line_count",
    "loop_path",
    "raw_segments",
]

#: 835 — one row per claim PAYMENT (CLP loop). `claim_id` is deliberately the
#: same column name as the 837's, because CLP01 echoes the submitter's CLM01:
#: that is the join that turns "we billed" + "they paid" into an underpayment.
REMIT_COLUMNS: list[str] = [
    *_ENVELOPE_COLUMNS,
    "payer_name",
    "payee_name",
    "check_or_eft_trace",
    "total_paid",
    "payment_method",
    "payment_date",
    "claim_id",
    "claim_status_code",
    "charged_amount",
    "paid_amount",
    "patient_responsibility",
    "adjustments",
    "service_line_count",
    "loop_path",
    "raw_segments",
]

#: 271 — one row per eligibility/benefit (EB) segment for a subscriber.
ELIGIBILITY_COLUMNS: list[str] = [
    *_ENVELOPE_COLUMNS,
    "information_source",     # 2100A payer/source name
    "subscriber_id",         # NM1 IL member id
    "subscriber_name",
    "benefit_status",        # EB01 eligibility/benefit code (1=active, 6=inactive, …)
    "coverage_level",        # EB02 (IND/FAM/…)
    "service_type",          # EB03 service type code
    "plan_description",      # EB05
    "benefit_amount",        # EB07 monetary amount
    "benefit_percent",       # EB08 percentage
    "loop_path",
    "raw_segments",
]

#: 277 — one row per claim status response (STC) for a claim.
CLAIM_STATUS_COLUMNS: list[str] = [
    *_ENVELOPE_COLUMNS,
    "information_source",    # 2100A payer name
    "provider_id",           # 2100C provider NM1
    "claim_id",              # TRN02 / REF claim identifier (echoes 837 CLM01)
    "status_category",       # STC01 first component: category code (A0/A1/…)
    "status_code",           # STC01 second component: status code
    "status_effective_date",  # STC02
    "total_charge",          # STC04
    "paid_amount",           # STC05
    "loop_path",
    "raw_segments",
]

#: 834 — one row per HD (health-coverage) line, with member + maintenance context.
ENROLLMENT_COLUMNS: list[str] = [
    *_ENVELOPE_COLUMNS,
    "member_id",
    "member_name",
    "maintenance_type",       # INS03 (021=add, 024=cancel, 030=audit, …)
    "maintenance_reason",     # INS04
    "coverage_type",          # HD03 insurance line (HLT/DEN/VIS)
    "plan_description",       # HD04
    "benefit_begin",          # DTP*348
    "benefit_end",            # DTP*349
    "loop_path",
    "raw_segments",
]

#: Back-compat alias: the 837 schema was the original single schema.
COLUMNS = CLAIM_COLUMNS


def _fail(msg: str) -> PermanentJobError:
    return PermanentJobError(ErrorCategory.DECODE_ERROR, msg)


@dataclass(slots=True)
class Delimiters:
    element: str
    component: str
    segment: str

    @classmethod
    def from_isa(cls, isa: str) -> Delimiters:
        """Derive the three delimiters from the fixed-width ISA header."""
        if len(isa) < ISA_LEN:
            raise _fail(
                f"malformed X12: ISA header is {len(isa)} chars, expected {ISA_LEN} "
                "(not a conformant interchange)"
            )
        if not isa.startswith("ISA"):
            raise _fail("malformed X12: stream does not begin with an ISA segment")
        return cls(element=isa[3], component=isa[104], segment=isa[105])


@dataclass(slots=True)
class _Envelope:
    """Control numbers carried onto every emitted row (BR-4)."""

    isa13: str = ""   # interchange control number
    isa06: str = ""   # sender id
    isa08: str = ""   # receiver id
    gs06: str = ""    # group control number
    st01: str = ""    # transaction set id
    st02: str = ""    # transaction set control number
    st_segment_count: int = 0


@dataclass(slots=True)
class _Claim:
    """One 2300 loop in flight (837)."""

    claim_id: str = ""
    total_charge: str = ""
    place_of_service: str = ""
    diagnosis_codes: list[str] = field(default_factory=list)
    service_lines: int = 0
    raw: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Remit:
    """One 2100 claim-payment loop in flight (835)."""

    claim_id: str = ""      # CLP01, echoes the submitter's CLM01
    status: str = ""        # CLP02 claim status code
    charged: str = ""       # CLP03 total submitted charge
    paid: str = ""          # CLP04 amount paid
    patient_resp: str = ""  # CLP05 patient responsibility
    service_lines: int = 0
    adjustments: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Benefit:
    """One EB (eligibility/benefit) row in flight (271)."""

    status: str = ""
    coverage_level: str = ""
    service_type: str = ""
    plan: str = ""
    amount: str = ""
    percent: str = ""
    raw: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ClaimStatus:
    """One STC (claim status) row in flight (277)."""

    claim_id: str = ""
    category: str = ""
    code: str = ""
    effective_date: str = ""
    total_charge: str = ""
    paid: str = ""
    raw: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Coverage:
    """One HD health-coverage line in flight (834)."""

    coverage_type: str = ""   # HD03
    plan: str = ""            # HD04
    begin: str = ""          # DTP*348
    end: str = ""            # DTP*349
    raw: list[str] = field(default_factory=list)


async def _segments(
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[tuple[list[str], str, Delimiters]]:
    """Tokenize the byte stream into (elements, raw_segment, delimiters).

    Yields nothing until the ISA is complete, since the delimiters needed to
    split anything at all are encoded in it.
    """
    buf = ""
    delims: Delimiters | None = None
    async for chunk in chunks:
        # X12 is ASCII; latin-1 is byte-preserving so a stray high byte cannot
        # raise mid-stream and lose the interchange.
        buf += chunk.decode("latin-1")
        if delims is None:
            if len(buf) < ISA_LEN:
                continue
            delims = Delimiters.from_isa(buf)
            isa_raw = buf[: ISA_LEN - 1]
            yield isa_raw.split(delims.element), isa_raw, delims
            buf = buf[ISA_LEN:]

        while True:
            idx = buf.find(delims.segment)
            if idx < 0:
                if len(buf) > MAX_SEGMENT_CHARS:
                    raise _fail(
                        f"malformed X12: segment exceeds {MAX_SEGMENT_CHARS} chars "
                        "(missing segment terminator or corrupt stream)"
                    )
                break
            raw = buf[:idx].strip("\r\n")
            buf = buf[idx + 1 :]
            if not raw:
                continue
            elements = raw.split(delims.element)
            if len(elements) > MAX_ELEMENTS_PER_SEGMENT:
                raise _fail(
                    f"malformed X12: segment {elements[0]!r} has {len(elements)} elements "
                    f"(cap {MAX_ELEMENTS_PER_SEGMENT})"
                )
            yield elements, raw, delims

    if delims is None:
        raise _fail("malformed X12: stream ended before a complete ISA header")
    if buf.strip("\r\n "):
        raise _fail("malformed X12: trailing data after the final segment terminator")


def _el(elements: list[str], i: int) -> str:
    return elements[i] if i < len(elements) else ""


def _env_prefix(env: _Envelope) -> list[Any]:
    """The six envelope-identity columns shared by every transaction set."""
    return [env.isa13, env.gs06, env.st02, env.st01, env.isa06, env.isa08]


class _ClaimHandler:
    """837 — accumulates one 2300 loop and emits one row per claim."""

    columns = CLAIM_COLUMNS

    def __init__(self, env: _Envelope) -> None:
        self.env = env
        self.claim: _Claim | None = None
        self.billing_npi = ""
        self.subscriber_id = ""

    def feed(self, tag: str, elements: list[str], raw: str, d: Delimiters, out: list) -> None:
        if self.claim is not None and len(self.claim.raw) < MAX_RAW_SEGMENTS_PER_CLAIM:
            self.claim.raw.append(raw)
        if tag == "CLM":
            self.flush(out)
            self.claim = _Claim(
                claim_id=_el(elements, 1).strip(),
                total_charge=_el(elements, 2).strip(),
                place_of_service=_el(elements, 5).split(d.component)[0].strip(),
                raw=[raw],
            )
        elif tag == "NM1":
            q = _el(elements, 1).strip()
            if q == "85":       # 2010AA billing provider
                self.billing_npi = _el(elements, 9).strip()
            elif q == "IL":     # 2010BA subscriber
                self.subscriber_id = _el(elements, 9).strip()
        elif tag == "HI" and self.claim is not None:
            for comp in elements[1:]:
                parts = comp.split(d.component)
                if len(parts) >= 2 and parts[1].strip():
                    self.claim.diagnosis_codes.append(parts[1].strip())
        elif tag == "SV1" and self.claim is not None:
            self.claim.service_lines += 1

    def flush(self, out: list) -> None:
        c = self.claim
        if c is None:
            return
        out.append([
            *_env_prefix(self.env),
            c.claim_id, c.total_charge, c.place_of_service,
            self.billing_npi, self.subscriber_id,
            ",".join(c.diagnosis_codes), c.service_lines,
            f"ISA/GS/ST({self.env.st01})/2300",
            "\n".join(c.raw[:MAX_RAW_SEGMENTS_PER_CLAIM]),
        ])
        self.claim = None


class _RemitHandler:
    """835 — one row per claim payment (CLP loop).

    Header context (payer/payee names, check/EFT trace, BPR payment) is captured
    once and repeated on each payment row, so a remittance row is self-describing
    without a second join. CLP01 echoes the submitter's claim id (CLM01), which
    is the key that lets an 835 row meet its 837 row.
    """

    columns = REMIT_COLUMNS

    def __init__(self, env: _Envelope) -> None:
        self.env = env
        self.payer = ""
        self.payee = ""
        self.trace = ""
        self.total_paid = ""
        self.pay_method = ""
        self.pay_date = ""
        self._nm1_ctx = ""
        self.clp: _Remit | None = None

    def feed(self, tag: str, elements: list[str], raw: str, d: Delimiters, out: list) -> None:
        if self.clp is not None and len(self.clp.raw) < MAX_RAW_SEGMENTS_PER_CLAIM:
            self.clp.raw.append(raw)
        if tag == "BPR":            # financial-information / payment header
            self.pay_method = _el(elements, 4).strip()
            self.total_paid = _el(elements, 2).strip()
            self.pay_date = _el(elements, 16).strip()
        elif tag == "TRN":          # reassociation trace (check/EFT number)
            self.trace = _el(elements, 2).strip()
        elif tag == "N1":           # payer (PR) / payee (PE) identification
            self._nm1_ctx = _el(elements, 1).strip()
            name = _el(elements, 2).strip()
            if self._nm1_ctx == "PR":
                self.payer = name
            elif self._nm1_ctx == "PE":
                self.payee = name
        elif tag == "CLP":          # 2100 claim payment loop
            self.flush(out)
            self.clp = _Remit(
                claim_id=_el(elements, 1).strip(),
                status=_el(elements, 2).strip(),
                charged=_el(elements, 3).strip(),
                paid=_el(elements, 4).strip(),
                patient_resp=_el(elements, 5).strip(),
                raw=[raw],
            )
        elif tag == "CAS" and self.clp is not None:   # claim-level adjustments
            grp = _el(elements, 1).strip()
            i = 2
            while i + 1 < len(elements) + 1 and _el(elements, i):
                code, amt = _el(elements, i).strip(), _el(elements, i + 1).strip()
                if code and amt:
                    self.clp.adjustments.append(f"{grp}:{code}:{amt}")
                i += 3  # CAS repeats in (code, amount, quantity) triplets
        elif tag == "SVC" and self.clp is not None:   # 2110 service payment line
            self.clp.service_lines += 1

    def flush(self, out: list) -> None:
        r = self.clp
        if r is None:
            return
        out.append([
            *_env_prefix(self.env),
            self.payer, self.payee, self.trace, self.total_paid,
            self.pay_method, self.pay_date,
            r.claim_id, r.status, r.charged, r.paid, r.patient_resp,
            ";".join(r.adjustments), r.service_lines,
            "ISA/GS/ST(835)/2100",
            "\n".join(r.raw[:MAX_RAW_SEGMENTS_PER_CLAIM]),
        ])
        self.clp = None


class _EligibilityHandler:
    """271 — subscriber eligibility/benefit response. One row per EB segment.

    Subscriber context (NM1 IL id + name) and the information source (2100A payer)
    are captured once and repeated on each benefit row, so a benefit line is
    self-describing. A 271 with no EB (e.g. AAA reject) yields zero rows — an
    honest empty result, not a failure.
    """

    columns = ELIGIBILITY_COLUMNS

    def __init__(self, env: _Envelope) -> None:
        self.env = env
        self.source = ""
        self.subscriber_id = ""
        self.subscriber_name = ""
        self._ctx = ""
        self.eb: _Benefit | None = None

    def feed(self, tag: str, elements: list[str], raw: str, d: Delimiters, out: list) -> None:
        if self.eb is not None and len(self.eb.raw) < MAX_RAW_SEGMENTS_PER_CLAIM:
            self.eb.raw.append(raw)
        if tag == "NM1":
            q = _el(elements, 1).strip()
            self._ctx = q
            if q in ("PR", "P5", "2B", "36"):     # information source (payer/plan)
                self.source = _el(elements, 3).strip()
            elif q == "IL":                        # subscriber
                last = _el(elements, 3).strip()
                first = _el(elements, 4).strip()
                self.subscriber_name = f"{last} {first}".strip()
                self.subscriber_id = _el(elements, 9).strip()
        elif tag == "EB":
            self.flush(out)
            self.eb = _Benefit(
                status=_el(elements, 1).strip(),
                coverage_level=_el(elements, 2).strip(),
                service_type=_el(elements, 3).strip(),
                plan=_el(elements, 5).strip(),
                amount=_el(elements, 7).strip(),
                percent=_el(elements, 8).strip(),
                raw=[raw],
            )

    def flush(self, out: list) -> None:
        b = self.eb
        if b is None:
            return
        out.append([
            *_env_prefix(self.env),
            self.source, self.subscriber_id, self.subscriber_name,
            b.status, b.coverage_level, b.service_type, b.plan, b.amount, b.percent,
            "ISA/GS/ST(271)/2110",
            "\n".join(b.raw[:MAX_RAW_SEGMENTS_PER_CLAIM]),
        ])
        self.eb = None


class _ClaimStatusHandler:
    """277 — claim status response. One row per STC segment.

    TRN02 (or REF) carries the claim identifier that echoes the 837's CLM01, so a
    277 row correlates back to the claim the same way an 835 does.
    """

    columns = CLAIM_STATUS_COLUMNS

    def __init__(self, env: _Envelope) -> None:
        self.env = env
        self.source = ""
        self.provider = ""
        self.claim_id = ""
        self._ctx = ""
        self.stc: _ClaimStatus | None = None

    def feed(self, tag: str, elements: list[str], raw: str, d: Delimiters, out: list) -> None:
        if self.stc is not None and len(self.stc.raw) < MAX_RAW_SEGMENTS_PER_CLAIM:
            self.stc.raw.append(raw)
        if tag == "NM1":
            q = _el(elements, 1).strip()
            self._ctx = q
            if q in ("PR", "AY", "41"):            # payer / information source
                self.source = _el(elements, 3).strip()
            elif q in ("1P", "85", "82"):          # provider
                self.provider = _el(elements, 9).strip()
        elif tag == "TRN":
            self.claim_id = _el(elements, 2).strip()
        elif tag == "REF" and _el(elements, 1).strip() in ("1K", "D9", "BLT"):
            # payer/clearinghouse claim control number if no TRN
            if not self.claim_id:
                self.claim_id = _el(elements, 2).strip()
        elif tag == "STC":
            self.flush(out)
            first = _el(elements, 1).split(d.component)
            self.stc = _ClaimStatus(
                claim_id=self.claim_id,
                category=first[0].strip() if first else "",
                code=first[1].strip() if len(first) > 1 else "",
                effective_date=_el(elements, 2).strip(),
                total_charge=_el(elements, 4).strip(),
                paid=_el(elements, 5).strip(),
                raw=[raw],
            )

    def flush(self, out: list) -> None:
        s = self.stc
        if s is None:
            return
        out.append([
            *_env_prefix(self.env),
            self.source, self.provider, s.claim_id,
            s.category, s.code, s.effective_date, s.total_charge, s.paid,
            "ISA/GS/ST(277)/2200",
            "\n".join(s.raw[:MAX_RAW_SEGMENTS_PER_CLAIM]),
        ])
        self.stc = None


class _EnrollmentHandler:
    """834 — benefit enrollment/maintenance. One row per HD coverage line.

    Member context (INS maintenance type/reason, member id from REF*0F, name from
    NM1*IL) is captured per member; each HD emits a coverage row carrying it, with
    DTP*348/349 benefit begin/end dates attached to the HD they follow.
    """

    columns = ENROLLMENT_COLUMNS

    def __init__(self, env: _Envelope) -> None:
        self.env = env
        self.member_id = ""
        self.member_name = ""
        self.maint_type = ""
        self.maint_reason = ""
        self.hd: _Coverage | None = None

    def feed(self, tag: str, elements: list[str], raw: str, d: Delimiters, out: list) -> None:
        if self.hd is not None and len(self.hd.raw) < MAX_RAW_SEGMENTS_PER_CLAIM:
            self.hd.raw.append(raw)
        if tag == "INS":
            self.flush(out)                       # close any open coverage of prior member
            self.maint_type = _el(elements, 3).strip()
            self.maint_reason = _el(elements, 4).strip()
        elif tag == "REF" and _el(elements, 1).strip() in ("0F", "1L", "17"):
            self.member_id = _el(elements, 2).strip()
        elif tag == "NM1" and _el(elements, 1).strip() == "IL":
            last, first = _el(elements, 3).strip(), _el(elements, 4).strip()
            self.member_name = f"{last} {first}".strip()
        elif tag == "HD":
            self.flush(out)
            self.hd = _Coverage(
                coverage_type=_el(elements, 3).strip(),
                plan=_el(elements, 4).strip(),
                raw=[raw],
            )
        elif tag == "DTP" and self.hd is not None:
            q = _el(elements, 1).strip()
            date = _el(elements, 3).strip()
            if q == "348":
                self.hd.begin = date
            elif q == "349":
                self.hd.end = date

    def flush(self, out: list) -> None:
        h = self.hd
        if h is None:
            return
        out.append([
            *_env_prefix(self.env),
            self.member_id, self.member_name, self.maint_type, self.maint_reason,
            h.coverage_type, h.plan, h.begin, h.end,
            "ISA/GS/ST(834)/2300",
            "\n".join(h.raw[:MAX_RAW_SEGMENTS_PER_CLAIM]),
        ])
        self.hd = None


_HANDLERS = {
    "837": _ClaimHandler,
    "835": _RemitHandler,
    "271": _EligibilityHandler,
    "277": _ClaimStatusHandler,
    "834": _EnrollmentHandler,
}


async def decode_x12(
    chunks: AsyncIterator[bytes], batch_size: int, stats: Any
) -> AsyncIterator[Any]:
    """Decode an X12 interchange into governed rows.

    This function owns ONLY the envelope (ISA/GS/ST..SE/GE/IEA + control-number
    conformance); the shape of each row is delegated to a per-transaction-set
    handler, so adding 834/276/… is a new handler, not a rewrite. `stats.rows_ok`
    advances per emitted row; envelope problems are fatal rather than per-row
    tolerated, because a broken envelope invalidates everything inside it.
    """
    from app.domain.tablewriter import RowBatch  # local: avoid an import cycle

    env = _Envelope()
    handler: Any | None = None
    rows: list[list[Any]] = []
    last_columns: list[str] = CLAIM_COLUMNS  # remembered so a post-SE flush knows the schema
    seen_iea = False
    segment_count = 0
    st_open = False

    async for elements, raw, delims in _segments(chunks):
        tag = elements[0].strip()
        segment_count += 1
        if segment_count > MAX_SEGMENTS_PER_TRANSACTION:
            raise _fail(
                f"malformed X12: interchange exceeds {MAX_SEGMENTS_PER_TRANSACTION} segments"
            )
        # SE01 carries the transaction's own segment count; tracking it lets us
        # detect a truncated or spliced transaction that still ends with a
        # well-formed SE (a corruption an ST02/SE02 match alone would miss).
        if st_open:
            env.st_segment_count += 1

        if tag == "ISA":
            env.isa06 = _el(elements, 6).strip()
            env.isa08 = _el(elements, 8).strip()
            env.isa13 = _el(elements, 13).strip()
        elif tag == "GS":
            env.gs06 = _el(elements, 6).strip()
        elif tag == "ST":
            st01 = _el(elements, 1).strip()
            if st01 not in SUPPORTED_TRANSACTION_SETS:
                known = " (recognised but not decoded by this build)" if st01 in KNOWN_TRANSACTION_SETS else ""  # noqa: E501
                raise _fail(
                    f"unsupported X12 transaction set {st01!r}{known}; "
                    f"this build decodes {', '.join(SUPPORTED_TRANSACTION_SETS)}"
                )
            env.st01, env.st02 = st01, _el(elements, 2).strip()
            env.st_segment_count = 1
            st_open = True
            handler = _HANDLERS[st01](env)
            last_columns = handler.columns
        elif tag == "SE":
            if handler is not None:
                handler.flush(rows)   # close the final in-flight loop of this ST
            handler = None
            se02 = _el(elements, 2).strip()
            if se02 != env.st02:
                raise _fail(
                    f"X12 control mismatch: SE02 {se02!r} != ST02 {env.st02!r} "
                    "(transaction set is not self-consistent)"
                )
            se01 = _el(elements, 1).strip()
            if se01.isdigit() and int(se01) != env.st_segment_count:
                raise _fail(
                    f"X12 conformance: SE01 declares {se01} segments but {env.st_segment_count} "
                    "were read (transaction truncated or spliced)"
                )
            st_open = False
        elif tag == "GE":
            ge02 = _el(elements, 2).strip()
            if ge02 != env.gs06:
                raise _fail(f"X12 control mismatch: GE02 {ge02!r} != GS06 {env.gs06!r}")
        elif tag == "IEA":
            iea02 = _el(elements, 2).strip()
            if iea02 != env.isa13:
                raise _fail(f"X12 control mismatch: IEA02 {iea02!r} != ISA13 {env.isa13!r}")
            seen_iea = True
        elif handler is not None:
            handler.feed(tag, elements, raw, delims, rows)

        # Rows in `rows` are already-completed loops (the in-flight one lives in
        # the handler), so a batch boundary never splits a claim. A single
        # interchange is one transaction-set type, so one schema per batch holds.
        if len(rows) >= batch_size:
            stats.rows_ok += len(rows)
            yield RowBatch(columns=list(last_columns), rows=rows)
            rows = []

    if st_open:
        raise _fail("malformed X12: transaction set (ST) never closed by an SE segment")
    if not seen_iea:
        raise _fail("malformed X12: interchange truncated (no IEA terminator)")
    if rows:
        stats.rows_ok += len(rows)
        yield RowBatch(columns=list(last_columns), rows=rows)

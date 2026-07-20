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
SUPPORTED_TRANSACTION_SETS = ("837", "835")
#: Recognised but not yet decoded — refused by name so the operator gets a real
#: reason rather than an empty dataset (BR-2/AC-8).
KNOWN_TRANSACTION_SETS = ("834", "270", "271", "276", "277", "997", "999")

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


_HANDLERS = {"837": _ClaimHandler, "835": _RemitHandler}


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

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
#: Transaction sets this build decodes into rows.
SUPPORTED_TRANSACTION_SETS = ("837",)
#: Recognised but not yet decoded — refused by name so the operator gets a real
#: reason rather than an empty dataset (BR-2/AC-8).
KNOWN_TRANSACTION_SETS = ("835", "834", "270", "271", "276", "277", "997", "999")

# --- hardening caps ---------------------------------------------------------
# X12 is attacker-reachable (a partner drops a file on our SFTP), so the parser
# is bounded the same way the XML decoder rejects DTDs and uploads cap bytes.
MAX_SEGMENT_CHARS = 100_000       # a single segment past this is corrupt/hostile
MAX_ELEMENTS_PER_SEGMENT = 2_000
MAX_SEGMENTS_PER_TRANSACTION = 5_000_000
MAX_RAW_SEGMENTS_PER_CLAIM = 2_000  # bounds the lineage column per row

COLUMNS: list[str] = [
    "interchange_control_number",
    "group_control_number",
    "transaction_control_number",
    "transaction_set",
    "sender_id",
    "receiver_id",
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
    """One 2300 loop in flight."""

    claim_id: str = ""
    total_charge: str = ""
    place_of_service: str = ""
    diagnosis_codes: list[str] = field(default_factory=list)
    service_lines: int = 0
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


async def decode_x12(
    chunks: AsyncIterator[bytes], batch_size: int, stats: Any
) -> AsyncIterator[Any]:
    """Decode an X12 interchange into one governed row per claim.

    `stats` is the caller's DecodeStats (rows_ok is advanced per emitted row);
    envelope problems are fatal rather than per-row tolerated, because a broken
    envelope invalidates everything inside it.
    """
    from app.domain.tablewriter import RowBatch  # local: avoid an import cycle

    env = _Envelope()
    claim: _Claim | None = None
    billing_npi = ""
    subscriber_id = ""
    rows: list[list[Any]] = []
    seen_iea = False
    segment_count = 0
    st_open = False

    def flush_claim() -> None:
        nonlocal claim
        if claim is None:
            return
        row = [
            env.isa13, env.gs06, env.st02, env.st01, env.isa06, env.isa08,
            claim.claim_id, claim.total_charge, claim.place_of_service,
            billing_npi, subscriber_id,
            ",".join(claim.diagnosis_codes), claim.service_lines,
            f"ISA/GS/ST({env.st01})/2300",
            "\n".join(claim.raw[:MAX_RAW_SEGMENTS_PER_CLAIM]),
        ]
        rows.append(row)
        stats.rows_ok += 1
        claim = None

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
        if claim is not None and len(claim.raw) < MAX_RAW_SEGMENTS_PER_CLAIM:
            # Bound the ACCUMULATION, not just the emitted slice: a hostile file
            # with one CLM followed by millions of segments would otherwise grow
            # this list unbounded before the row is ever flushed.
            claim.raw.append(raw)

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
        elif tag == "SE":
            flush_claim()
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
        elif tag == "CLM":
            flush_claim()
            claim = _Claim(
                claim_id=_el(elements, 1).strip(),
                total_charge=_el(elements, 2).strip(),
                place_of_service=_el(elements, 5).split(delims.component)[0].strip(),
                raw=[raw],
            )
        elif tag == "NM1":
            qualifier = _el(elements, 1).strip()
            if qualifier == "85":       # 2010AA billing provider
                billing_npi = _el(elements, 9).strip()
            elif qualifier == "IL":     # 2010BA subscriber
                subscriber_id = _el(elements, 9).strip()
        elif tag == "HI" and claim is not None:
            for comp in elements[1:]:
                parts = comp.split(delims.component)
                if len(parts) >= 2 and parts[1].strip():
                    claim.diagnosis_codes.append(parts[1].strip())
        elif tag == "SV1" and claim is not None:
            claim.service_lines += 1

        if len(rows) >= batch_size:
            yield RowBatch(columns=list(COLUMNS), rows=rows)
            rows = []

    flush_claim()
    if st_open:
        raise _fail("malformed X12: transaction set (ST) never closed by an SE segment")
    if not seen_iea:
        raise _fail("malformed X12: interchange truncated (no IEA terminator)")
    if rows:
        yield RowBatch(columns=list(COLUMNS), rows=rows)

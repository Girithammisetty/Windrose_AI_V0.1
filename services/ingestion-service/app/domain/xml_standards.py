"""ISO 20022 + ACORD decoding (BRD 57 inc-3e, STD-FR-030/031, BR-2/BR-4).

Both are XML, so — unlike X12/HL7 which are bespoke wire grammars — the parsing
and the DoS hardening already exist in ``decode.py`` (DTD/billion-laughs rejection
+ bounded temp-file spool + namespace-stripping). This module REUSES that
hardening and adds only the semantic mapping: which elements of a camt statement
or an ACORD document become governed columns. It never re-implements XML parsing
and never relaxes the DTD guard.

* **ISO 20022** ``camt.052/053/054`` — bank/account statements. One row per
  ``Ntry`` (statement entry) with amount, credit/debit indicator, booking date
  and remittance info, plus the statement/account identity.
* **ACORD** — P&C application / loss-run XML. One row per policy-bearing element
  with the policy number, insured, LOB and effective/expiry dates.

Dispatch is by the document's root local-name, so a file that is neither is
refused by name (Rule 2) rather than flattened into meaningless rows.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosemgrep: use-defused-xml  (DTD rejected below)
from collections.abc import AsyncIterator
from typing import Any

from app.domain.decode import _reject_dtd, _spool_to_tempfile, _xml_localname
from app.domain.errors import ErrorCategory, PermanentJobError

MAX_ROWS_GUARD = 50_000_000  # backstop against a pathological element count

ISO20022_COLUMNS: list[str] = [
    "statement_id",
    "account_id",
    "entry_ref",
    "amount",
    "currency",
    "credit_debit",
    "status",
    "booking_date",
    "value_date",
    "remittance_info",
]

ACORD_COLUMNS: list[str] = [
    "doc_type",
    "policy_number",
    "insured_name",
    "line_of_business",
    "effective_date",
    "expiry_date",
    "premium",
    "carrier",
]


def _fail(msg: str) -> PermanentJobError:
    return PermanentJobError(ErrorCategory.DECODE_ERROR, msg)


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _find(parent: ET.Element, *localnames: str) -> ET.Element | None:
    """Namespace-agnostic descendant find by a path of local-names."""
    cur = parent
    for name in localnames:
        nxt = None
        for child in cur.iter():
            if child is not cur and _xml_localname(child.tag) == name:
                nxt = child
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _first_text(parent: ET.Element, localname: str) -> str:
    for el in parent.iter():
        if el is not parent and _xml_localname(el.tag) == localname:
            return _text(el)
    return ""


def _iter_local(root: ET.Element, localname: str) -> list[ET.Element]:
    return [el for el in root.iter() if _xml_localname(el.tag) == localname]


def _sub_text(parent: ET.Element, container: str, leaf: str) -> str:
    """Text of `leaf` within the `container` subtree, or "". Explicit `is None`
    checks throughout — an ElementTree element with no children is FALSY, so
    `_find(...) or parent` would silently mis-scope (and is deprecated)."""
    node = _find(parent, container)
    return _first_text(node, leaf) if node is not None else ""


def _map_iso20022_entry(ntry: ET.Element, stmt_id: str, account: str) -> list[Any]:
    amt_el = _find(ntry, "Amt")
    return [
        stmt_id,
        account,
        _first_text(ntry, "NtryRef") or _first_text(ntry, "AcctSvcrRef"),
        _text(amt_el),
        (amt_el.get("Ccy") if amt_el is not None else "") or "",
        _first_text(ntry, "CdtDbtInd"),
        _first_text(ntry, "Sts"),
        _sub_text(ntry, "BookgDt", "Dt"),
        _sub_text(ntry, "ValDt", "Dt"),
        _first_text(ntry, "Ustrd"),
    ]


def _map_acord_policy(el: ET.Element) -> list[Any]:
    return [
        "ACORD",
        _first_text(el, "PolicyNumber") or _first_text(el, "ContractNumber"),
        _first_text(el, "CommercialName") or _first_text(el, "InsuredName")
        or _first_text(el, "GivenName"),
        _first_text(el, "LOBCd") or _first_text(el, "LineOfBusiness"),
        _first_text(el, "EffectiveDt") or _first_text(el, "EffectiveDate"),
        _first_text(el, "ExpirationDt") or _first_text(el, "ExpiryDate"),
        # FullTermAmt wraps an <Amt> leaf in ACORD; fall back to a flat Premium.
        _sub_text(el, "FullTermAmt", "Amt") or _first_text(el, "Premium"),
        _first_text(el, "NAICCd") or _first_text(el, "CarrierName"),
    ]


async def decode_iso20022(
    chunks: AsyncIterator[bytes], batch_size: int, stats: Any
) -> AsyncIterator[Any]:
    """Decode an ISO 20022 camt.05x statement into one row per entry."""
    async for batch in _decode(chunks, batch_size, stats, "iso20022"):
        yield batch


async def decode_acord(
    chunks: AsyncIterator[bytes], batch_size: int, stats: Any
) -> AsyncIterator[Any]:
    """Decode an ACORD P&C document into one row per policy element."""
    async for batch in _decode(chunks, batch_size, stats, "acord"):
        yield batch


async def _decode(
    chunks: AsyncIterator[bytes], batch_size: int, stats: Any, kind: str
) -> AsyncIterator[Any]:
    from app.domain.tablewriter import RowBatch  # local: avoid import cycle

    path = await _spool_to_tempfile(chunks)
    try:
        _reject_dtd(path)  # billion-laughs guard BEFORE parsing content (reused)
        try:
            # Safe post-guard: _reject_dtd bails on any DOCTYPE (the only place
            # internal entities can be defined) and stdlib expat never resolves
            # external entities — same reviewed posture as decode.py.
            tree = ET.parse(str(path))  # nosemgrep: python.lang.security.use-defused-xml-parse.use-defused-xml-parse  # noqa: E501
        except ET.ParseError as e:
            raise _fail(f"{kind}: not well-formed XML ({e})") from e
        root = tree.getroot()
        root_name = _xml_localname(root.tag)

        rows: list[list[Any]] = []
        if kind == "iso20022":
            if root_name != "Document":
                raise _fail(
                    f"iso20022: root element is {root_name!r}, expected 'Document' "
                    "(not an ISO 20022 message)"
                )
            columns = ISO20022_COLUMNS
            stmts = _iter_local(root, "Stmt") or [root]
            for stmt in stmts:
                stmt_id = _first_text(stmt, "Id")
                account = _sub_text(stmt, "Acct", "IBAN") or _sub_text(stmt, "Acct", "Id")
                for ntry in _iter_local(stmt, "Ntry"):
                    rows.append(_map_iso20022_entry(ntry, stmt_id, account))
                    if len(rows) >= batch_size:
                        stats.rows_ok += len(rows)
                        yield RowBatch(columns=list(columns), rows=rows)
                        rows = []
        else:  # acord
            if "ACORD" not in root_name:
                raise _fail(
                    f"acord: root element is {root_name!r}, expected an ACORD root "
                    "(not an ACORD document)"
                )
            columns = ACORD_COLUMNS
            policies = (
                _iter_local(root, "PersPolicy")
                + _iter_local(root, "CommlPolicy")
                + _iter_local(root, "Policy")
            )
            for pol in policies:
                rows.append(_map_acord_policy(pol))
                if len(rows) >= batch_size:
                    stats.rows_ok += len(rows)
                    yield RowBatch(columns=list(columns), rows=rows)
                    rows = []

        if len(rows) > MAX_ROWS_GUARD:
            raise _fail(f"{kind}: element count exceeds {MAX_ROWS_GUARD}")
        if rows:
            stats.rows_ok += len(rows)
            yield RowBatch(columns=list(columns), rows=rows)
    finally:
        path.unlink(missing_ok=True)

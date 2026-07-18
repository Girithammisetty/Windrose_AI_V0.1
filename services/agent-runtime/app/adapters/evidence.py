"""Case-evidence reader — turns a case's attached documents into text an agent
can reason over (the follow-up to task #77, which shipped attach/list/download).

The attach/list/download of evidence files is already live in case-service
(MinIO-backed, ``case.evidence.*`` RBAC). What was missing is the agent being
able to READ those unstructured documents: a triage decision needs more than the
structured row projection — it needs the discharge summary, the 835 remittance,
the dispute letter. This adapter lists a case's evidence, downloads each file,
and extracts text so the LangGraph grounding step can put the ACTUAL document
content in front of the model, which then cites the source filename.

Honest boundaries (recorded, never faked):
  * text/*, application/json, application/xml, *+xml, text/csv → decoded directly.
  * application/pdf → extracted with pypdf (pure-python; no native deps).
  * images (image/*) and anything else → NOT extracted; returned with
    ``extracted=False`` + a ``note`` so the agent (and the trace) can SEE that a
    document exists but its content was not read. OCR is the follow-up.
  * Bounded: at most ``max_docs`` files, ``max_chars_per_doc`` per file, and
    ``max_total_chars`` overall — large corpora need chunk+embed retrieval
    (the scale follow-up); this increment injects bounded full-text.
"""

from __future__ import annotations

import io
import json
import logging

log = logging.getLogger("agent_runtime.evidence")

# content types we can decode straight to text
_TEXT_PREFIXES = ("text/",)
_TEXT_EXACT = {"application/json", "application/xml", "application/xhtml+xml",
               "application/x-ndjson", "application/csv"}


def _is_textual(ct: str) -> bool:
    ct = (ct or "").split(";")[0].strip().lower()
    return ct.startswith(_TEXT_PREFIXES) or ct in _TEXT_EXACT or ct.endswith("+xml") \
        or ct.endswith("+json")


def _extract_pdf(data: bytes, max_chars: int) -> str:
    from pypdf import PdfReader  # imported lazily so the module loads without a PDF

    reader = PdfReader(io.BytesIO(data))
    out: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — a single bad page must not sink the doc
            txt = ""
        if not txt:
            continue
        out.append(txt)
        total += len(txt)
        if total >= max_chars:
            break
    return "\n".join(out)


def extract_text(data: bytes, content_type: str, *, max_chars: int) -> tuple[str, bool, str]:
    """(text, extracted, note). ``extracted=False`` means the content type is not
    text-extractable here; ``note`` says why. Never raises for a single file."""
    ct = (content_type or "").split(";")[0].strip().lower()
    try:
        if ct == "application/pdf":
            txt = _extract_pdf(data, max_chars)
            if not txt.strip():
                return "", False, "pdf had no extractable text (likely scanned image — OCR is a follow-up)"  # noqa: E501
            return txt[:max_chars], True, ""
        if _is_textual(ct):
            txt = data.decode("utf-8", errors="replace")
            if ct in ("application/json", "application/x-ndjson"):
                # pretty a little so the model sees structure, but tolerate non-JSON
                try:
                    txt = json.dumps(json.loads(txt), indent=1)[:max_chars]
                except Exception:  # noqa: BLE001
                    pass
            return txt[:max_chars], True, ""
        if ct.startswith("image/"):
            return "", False, "image evidence — not text-extractable (OCR is a follow-up)"
        return "", False, f"content type {ct or 'unknown'} not text-extractable"
    except Exception as exc:  # noqa: BLE001 — defensive: never fail the run on one file
        log.warning("evidence extraction failed ct=%s: %s", ct, exc)
        return "", False, f"extraction error: {type(exc).__name__}"


class EvidenceReader:
    """Real EvidenceReader: lists + downloads a case's evidence via a case client
    (``list_evidence`` / ``download_evidence``) and extracts text. Kept separate
    from the case-reader so grounding on documents is an explicit, bounded step."""

    def __init__(self, case_client, *, max_docs: int = 5,
                 max_chars_per_doc: int = 4000, max_total_chars: int = 12000) -> None:
        self._client = case_client
        self._max_docs = max_docs
        self._max_chars_per_doc = max_chars_per_doc
        self._max_total_chars = max_total_chars

    async def read_case_evidence(self, *, tenant_id: str, case_id: str,
                                 auth_token: str) -> list[dict]:
        try:
            meta = await self._client.list_evidence(
                tenant_id=tenant_id, case_id=case_id, auth_token=auth_token)
        except Exception as exc:  # noqa: BLE001 — grounding is best-effort
            log.warning("list_evidence failed case=%s: %s", case_id, exc)
            return []

        docs: list[dict] = []
        total = 0
        for m in meta[: self._max_docs]:
            eid = m.get("id")
            filename = m.get("filename") or "evidence"
            content_type = m.get("content_type") or "application/octet-stream"
            size = int(m.get("size_bytes") or 0)
            rec = {"id": eid, "filename": filename, "content_type": content_type,
                   "size_bytes": size, "text": "", "extracted": False, "note": ""}
            budget = min(self._max_chars_per_doc, max(0, self._max_total_chars - total))
            if budget <= 0:
                rec["note"] = "skipped — evidence text budget exhausted"
                docs.append(rec)
                continue
            try:
                data, dl_ct = await self._client.download_evidence(
                    tenant_id=tenant_id, case_id=case_id, evidence_id=eid,
                    auth_token=auth_token)
            except Exception as exc:  # noqa: BLE001
                rec["note"] = f"download failed: {type(exc).__name__}"
                docs.append(rec)
                continue
            text, ok, note = extract_text(data, dl_ct or content_type,
                                          max_chars=budget)
            rec.update(text=text, extracted=ok, note=note)
            total += len(text)
            docs.append(rec)
        return docs

"""Case-evidence reader: text extraction + the list→download→extract pipeline.

Proves the agent can turn a case's attached documents into text it can reason
over (the follow-up to attach/list/download #77): text/json/xml decode directly,
PDFs extract via pypdf, images are honestly marked not-extractable (OCR follow-up),
and the whole read is bounded (doc count + per-doc + total char budget).
"""

from __future__ import annotations

from app.adapters.evidence import EvidenceReader, extract_text


# ---- a minimal but VALID pdf with a real text-showing operator --------------
def _minimal_pdf(text: str) -> bytes:
    """Hand-build a one-page PDF whose content stream draws `text`, with a
    correct xref table so pypdf parses it. Avoids a heavyweight PDF-writer dep."""
    content = f"BT /F1 12 Tf 20 120 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\n"
            f"startxref\n{xref_at}\n%%EOF").encode()
    return bytes(out)


def test_extract_plain_and_structured_text():
    t, ok, note = extract_text(b"discharge: acute MI", "text/plain", max_chars=100)
    assert ok and "acute MI" in t and note == ""

    t, ok, _ = extract_text(b'{"code":"99490","minutes":22}', "application/json", max_chars=200)
    assert ok and "99490" in t

    t, ok, _ = extract_text(b"<claim><amount>760.40</amount></claim>", "application/xml", max_chars=200)  # noqa: E501
    assert ok and "760.40" in t

    t, ok, _ = extract_text(b"a,b\n1,2", "text/csv", max_chars=100)
    assert ok and "a,b" in t


def test_extract_truncates_to_budget():
    t, ok, _ = extract_text(b"x" * 5000, "text/plain", max_chars=100)
    assert ok and len(t) == 100


def test_image_is_not_extractable_but_flagged():
    t, ok, note = extract_text(b"\x89PNG\r\n", "image/png", max_chars=100)
    assert not ok and t == "" and "OCR" in note


def test_unknown_type_flagged_not_extractable():
    t, ok, note = extract_text(b"\x00\x01", "application/octet-stream", max_chars=100)
    assert not ok and "not text-extractable" in note


def test_extract_real_pdf_text():
    pdf = _minimal_pdf("INV-5540 duplicate of INV-5540")
    t, ok, note = extract_text(pdf, "application/pdf", max_chars=2000)
    assert ok, f"pdf not extracted: {note}"
    assert "INV-5540" in t


# ---- the list -> download -> extract pipeline (bounded) ---------------------
class _FakeCaseClient:
    """case client double exposing list_evidence + download_evidence."""

    def __init__(self, files: list[dict]) -> None:
        # files: [{id, filename, content_type, size_bytes, bytes}]
        self._files = files
        self.downloads: list[str] = []

    async def list_evidence(self, *, tenant_id, case_id, auth_token) -> list[dict]:
        return [{k: f[k] for k in ("id", "filename", "content_type", "size_bytes")}
                for f in self._files]

    async def download_evidence(self, *, tenant_id, case_id, evidence_id, auth_token):
        self.downloads.append(evidence_id)
        f = next(f for f in self._files if f["id"] == evidence_id)
        return f["bytes"], f["content_type"]


async def test_reader_lists_downloads_and_extracts():
    client = _FakeCaseClient([
        {"id": "e1", "filename": "discharge.txt", "content_type": "text/plain",
         "size_bytes": 30, "bytes": b"acute MI, discharged home 2026-05-21"},
        {"id": "e2", "filename": "photo.png", "content_type": "image/png",
         "size_bytes": 10, "bytes": b"\x89PNG..."},
    ])
    reader = EvidenceReader(client)
    docs = await reader.read_case_evidence(tenant_id="t", case_id="c-1", auth_token="tok")

    assert [d["filename"] for d in docs] == ["discharge.txt", "photo.png"]
    txt_doc = docs[0]
    assert txt_doc["extracted"] and "acute MI" in txt_doc["text"]
    img_doc = docs[1]
    assert not img_doc["extracted"] and "OCR" in img_doc["note"]
    assert client.downloads == ["e1", "e2"]  # both fetched


async def test_reader_respects_doc_and_total_budgets():
    files = [{"id": f"e{i}", "filename": f"d{i}.txt", "content_type": "text/plain",
              "size_bytes": 100, "bytes": b"y" * 100} for i in range(10)]
    reader = EvidenceReader(_FakeCaseClient(files), max_docs=3,
                            max_chars_per_doc=50, max_total_chars=120)
    docs = await reader.read_case_evidence(tenant_id="t", case_id="c-1", auth_token="tok")
    assert len(docs) == 3                       # max_docs cap
    assert all(len(d["text"]) <= 50 for d in docs)   # per-doc cap
    assert sum(len(d["text"]) for d in docs) <= 120  # total cap


async def test_reader_survives_list_failure():
    class _Boom:
        async def list_evidence(self, **_):
            raise RuntimeError("case-service down")

    docs = await EvidenceReader(_Boom()).read_case_evidence(
        tenant_id="t", case_id="c-1", auth_token="tok")
    assert docs == []  # best-effort: no evidence rather than a crashed run

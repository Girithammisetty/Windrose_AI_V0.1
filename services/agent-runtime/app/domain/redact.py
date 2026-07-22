"""Lightweight PII redaction for the SLM transcript corpus.

Milestone-1, dependency-free redaction applied BEFORE a transcript is persisted,
so obvious direct identifiers (emails, phones, SSNs, card-like numbers, IPs)
never land in the training corpus. It walks arbitrary JSON (dicts/lists/strings)
and replaces matches with a typed token (``[REDACTED:email]`` …). This is a
floor, not a ceiling — the design's curation stage layers the tenant's richer
PII tagging on top before any model trains; here we guarantee the raw capture is
already scrubbed of the common direct identifiers.
"""

from __future__ import annotations

import re
from typing import Any

_STREET_SUFFIXES = (
    r"Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|"
    r"Circle|Cir|Way|Place|Pl|Terrace|Ter|Highway|Hwy|Parkway|Pkwy|Square|Sq|Trail|Trl"
)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # 13-19 digit card-like runs (optionally separated), checked before phones
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("phone", re.compile(r"\b(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # Street address (number + street name + common suffix) — a real, if narrow,
    # structural pattern (BRD 58 SEC-5). Does not catch every address shape
    # (PO boxes, non-US formats), same "floor, not ceiling" caveat as the rest.
    ("address", re.compile(
        rf"\b\d{{1,6}}\s+(?:[A-Za-z0-9]+\s){{0,4}}(?:{_STREET_SUFFIXES})\.?\b", re.I
    )),
    # Honorific-prefixed name (Mr./Dr. Jane Doe) — catches a real, common subset
    # of person names in formal text; general name detection still needs NER
    # (unchanged limitation, documented above).
    ("name", re.compile(
        r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Rev)\.?\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,2}\b"
    )),
]

_MAX_STR = 8000  # cap any single captured string so a transcript stays bounded


def redact_text(s: str) -> str:
    for label, pat in _PATTERNS:
        s = pat.sub(f"[REDACTED:{label}]", s)
    if len(s) > _MAX_STR:
        s = s[:_MAX_STR] + "…[truncated]"
    return s


def redact(value: Any) -> Any:
    """Deep-redact a JSON-ish value (str/dict/list/scalars)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value

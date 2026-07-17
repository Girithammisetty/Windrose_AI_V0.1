"""PII scanning + anonymization (MEM-FR-010 step 3, MEM-FR-031, AC-6).

Real, deterministic recognizers over a fixed PII taxonomy — the local,
network-free equivalent of the Presidio pass the platform runs (BRD §3
resolved-cases anonymization). ``RegexPiiScanner`` reports which disallowed
classes appear (write-path reject); ``RegexAnonymizer`` irreversibly redacts
matched spans BEFORE embedding (BR-9) for corpus ingestion.
"""

from __future__ import annotations

import re

# class -> detector
_DETECTORS: dict[str, re.Pattern] = {
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "PHONE": re.compile(r"\b(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}\b"),
    "PERSON": re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"),
}


class RegexPiiScanner:
    async def scan(self, text: str, classes: list[str]) -> list[str]:
        body = text or ""
        found: list[str] = []
        for cls in classes:
            det = _DETECTORS.get(cls.upper())
            if det and det.search(body):
                found.append(cls.upper())
        return found


class RegexAnonymizer:
    """Redacts configured field drops + a standard detector pass. Irreversible:
    the returned text is what gets embedded (BR-9)."""

    async def anonymize(self, text: str, profile: dict | None) -> str:
        body = text or ""
        profile = profile or {}
        drop = [c.upper() for c in profile.get("drop_classes", [])] or [
            "PERSON", "EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IBAN",
        ]
        for cls in drop:
            det = _DETECTORS.get(cls)
            if det:
                body = det.sub(f"[{cls}]", body)
        return body

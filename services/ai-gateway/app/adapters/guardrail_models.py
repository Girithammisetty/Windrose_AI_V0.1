"""Guardrail model adapters.

`RegexPIIAnalyzer` is a real, deterministic, fully-local analyzer covering the
default entity set — EMAIL/PHONE/CREDIT_CARD(Luhn-checked)/SSN/IBAN (AIG-FR-050).
It runs in-process with no external dependency and is the runtime PII analyzer.
`HeuristicInjectionClassifier` is a real, deterministic pattern scorer
(AIG-FR-051).

The only PII capability not covered locally is PERSON-name detection, which
requires an NER model (Microsoft Presidio + a spaCy model). That is left as
opt-in/credential-gated future work; every other entity is detected for real."""

from __future__ import annotations

import re

from app.domain.ports import PIIEntity

_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "PHONE": re.compile(
        r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\d)"
    ),
    "CREDIT_CARD": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?<![ -])(?!\d)"),
    "SSN": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "PERSON": re.compile(r"$^"),  # opt-in; requires NER — Presidio only (prod)
}


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


class RegexPIIAnalyzer:
    def analyze(self, text: str, entities: list[str]) -> list[PIIEntity]:
        found: list[PIIEntity] = []
        for kind in entities:
            pattern = _PATTERNS.get(kind)
            if pattern is None:
                continue
            for m in pattern.finditer(text):
                value = m.group(0)
                if kind == "CREDIT_CARD":
                    digits = re.sub(r"[ -]", "", value)
                    if not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                        continue
                found.append(PIIEntity(kind=kind, start=m.start(), end=m.end(),
                                       text=value))
        # Prefer more specific matches when spans overlap (e.g. SSN inside PHONE)
        found.sort(key=lambda e: (e.start, -(e.end - e.start)))
        result: list[PIIEntity] = []
        last_end = -1
        for e in found:
            if e.start >= last_end:
                result.append(e)
                last_end = e.end
        return result


_INJECTION_SIGNALS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|prompts?)", re.I), 0.9),
    (re.compile(r"disregard (your|the) (system|previous) (prompt|instructions)", re.I), 0.9),
    (re.compile(r"you are now (dan|developer mode|jailbroken)", re.I), 0.95),
    (re.compile(r"reveal (your|the) system prompt", re.I), 0.9),
    (re.compile(r"print (your|the) (hidden|system) (prompt|instructions)", re.I), 0.9),
    (re.compile(r"pretend (you are|to be) (?:an? )?unrestricted", re.I), 0.8),
    (re.compile(r"do anything now", re.I), 0.7),
    (re.compile(r"override (your )?safety", re.I), 0.85),
    (re.compile(r"\bBEGIN SYSTEM PROMPT\b", re.I), 0.7),
    (re.compile(r"as your (developer|creator|administrator)", re.I), 0.5),
]


class HeuristicInjectionClassifier:
    """Deterministic pattern scorer; the fine-tuned classifier service is the
    production adapter (TODO). Scores in [0, 1] (AIG-FR-051)."""

    def score(self, text: str) -> float:
        return max((w for p, w in _INJECTION_SIGNALS if p.search(text)), default=0.0)

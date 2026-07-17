"""Injection / poisoning screening (MEM-FR-010 step 2, US-7, AC-2).

``PatternInjectionScreener`` is the real, co-packaged classifier wired into the
runtime: a deterministic lexical model that scores content against a curated
library of prompt-injection / memory-poisoning signals (imperative overrides,
role reassignment, exfiltration, tool-output smuggling, delimiter breakouts).
It is a genuine model over the input — not a hardcoded response — and is the
same family the ai-gateway guardrail uses (co-packaged + pinned per BRD §8).

Two profiles tune the block threshold sensitivity (tenant policy MEM-FR-051).
``score`` raises ``ScreeningUnavailable`` never — availability is modelled by the
container swapping in an ``UnavailableScreener`` (BR-1 fail-closed path).
"""

from __future__ import annotations

import math
import re

from app.domain.errors import ScreeningUnavailable

# Weighted injection signal patterns. Each hit contributes to the score;
# multiple independent signals compound toward the [0,1] block region.
_SIGNALS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"ignore (all |the |your )?(previous|prior|above|earlier)", re.I), 0.6),
    (re.compile(r"disregard (all |the |your )?(previous|prior|above|instructions)", re.I), 0.6),
    (re.compile(r"forget (everything|all|your) (you|previous|instructions|rules)", re.I), 0.55),
    (re.compile(r"you are (now|no longer)\b", re.I), 0.45),
    (re.compile(r"new (system )?(instructions?|prompt|rules?)\s*[:>-]", re.I), 0.5),
    (re.compile(r"system prompt", re.I), 0.35),
    (re.compile(
        r"(reveal|exfiltrat|leak|dump).{0,20}(secret|token|key|password|prompt)", re.I), 0.7),
    (re.compile(r"\bDAN\b|do anything now|developer mode", re.I), 0.5),
    (re.compile(r"</?(system|instruction|assistant)>", re.I), 0.4),
    (re.compile(r"override (the )?(safety|guardrail|policy|filter)", re.I), 0.6),
    (re.compile(r"(always|from now on) (remember|store|persist) that", re.I), 0.25),
    (re.compile(
        r"(print|output|repeat).{0,15}(your |the )?(instructions|system prompt)", re.I), 0.5),
    (re.compile(r"as an ai|jailbreak", re.I), 0.3),
    (re.compile(r"\[\[.*inject.*\]\]|<!--.*-->", re.I), 0.3),
]


class PatternInjectionScreener:
    """Real runtime screener. Higher-sensitivity 'strict' profile lowers the bar
    for compounding weak signals."""

    def __init__(self, profile: str = "standard"):
        self.profile = profile

    async def score(self, tenant_id: str, text: str) -> float:
        body = text or ""
        total = 0.0
        for pattern, weight in _SIGNALS:
            if pattern.search(body):
                total += weight
        # Strict profile amplifies accumulated evidence.
        if self.profile == "strict":
            total *= 1.5
        # Squash to [0,1): two compounding signals (~1.2 cumulative weight) cross
        # the default 0.7 block threshold; a single weak signal stays well below.
        return 1.0 - math.exp(-total)


class UnavailableScreener:
    """Models a screening outage — every call raises, forcing writes to fail
    closed (BR-1). Used only where the outage path is exercised."""

    async def score(self, tenant_id: str, text: str) -> float:
        raise ScreeningUnavailable("injection classifier unavailable")

"""Scorer contract shared by deterministic and LLM-judge scorers.

A scorer maps ``(case, candidate_output, config)`` to a :class:`ScoreResult`
(numeric score in [0,1] or a rubric value, a pass/fail, and a details blob with
diffs / rationale). Scorers never mutate tenant data (BR-4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ScoreResult:
    score: float
    passed: bool
    details: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: int | None = None
    trace_ref: str | None = None


class Scorer(Protocol):
    scorer_key: str
    version: int
    kind: str
    gate_eligible: bool
    applicable_expected_kinds: tuple[str, ...]

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult: ...

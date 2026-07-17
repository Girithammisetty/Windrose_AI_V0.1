"""Decision outcome labels + effectiveness (BRD 55).

A pure domain layer: the OutcomeLabel record and the effectiveness aggregation
(decided-vs-realized agreement, sliced by decision type + producer). No I/O —
the store persists, the API captures, this computes. Correlational effectiveness
only (BR-3): agreement, never a causal claim.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(slots=True)
class OutcomeLabel:
    label_id: str
    tenant_id: str
    decision_ref: str
    decision_type: str
    realized_outcome: str
    decided_outcome: str | None = None
    correct: bool | None = None
    label_source: str = "human"   # human | sor | event
    note: str | None = None
    labeled_by: str | None = None
    producer: str | None = None


LABEL_SOURCES = ("human", "sor", "event")


def compute_correct(decided: str | None, realized: str) -> bool | None:
    """None when we can't compare (no decided outcome recorded); else a
    case-insensitive equality — the correlational agreement signal."""
    if decided is None or str(decided).strip() == "":
        return None
    return str(decided).strip().lower() == str(realized).strip().lower()


@dataclass(slots=True)
class EffectivenessRow:
    key: str
    total: int = 0
    correct: int = 0
    incorrect: int = 0
    unknown: int = 0

    @property
    def effectiveness_rate(self) -> float | None:
        scored = self.correct + self.incorrect
        return round(self.correct / scored, 4) if scored else None

    def to_dict(self) -> dict:
        return {"key": self.key, "total": self.total, "correct": self.correct,
                "incorrect": self.incorrect, "unknown": self.unknown,
                "effectiveness_rate": self.effectiveness_rate}


def effectiveness(labels: list[OutcomeLabel], *, by: str = "decision_type") -> list[dict]:
    """Aggregate agreement (decided vs realized) grouped by ``decision_type`` or
    ``producer``. Rows sorted by total desc. Deterministic (BR-2/BR-5)."""
    buckets: dict[str, EffectivenessRow] = defaultdict(lambda: EffectivenessRow(key=""))
    for lab in labels:
        k = (lab.decision_type if by == "decision_type" else (lab.producer or "unknown"))
        row = buckets[k]
        row.key = k
        row.total += 1
        if lab.correct is True:
            row.correct += 1
        elif lab.correct is False:
            row.incorrect += 1
        else:
            row.unknown += 1
    return [r.to_dict() for r in sorted(buckets.values(),
                                        key=lambda r: r.total, reverse=True)]

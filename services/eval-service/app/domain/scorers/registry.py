"""Built-in scorer registry (EVL-FR-010). Maps scorer_key -> instance and
exposes metadata used by suite validation and gate evaluation."""

from __future__ import annotations

from .deterministic import (
    CostCeilingScorer,
    LatencyCeilingScorer,
    ProposalMatchScorer,
    SchemaValidityScorer,
    SqlResultEquivalenceScorer,
    ToolSelectionAccuracyScorer,
)
from .judge import GroundednessJudgeScorer, HelpfulnessJudgeScorer

# Scorers that gate alone (deterministic). Judge scorers are excluded (BR-1).
DETERMINISTIC_KEYS = frozenset(
    {
        "sql_result_equivalence",
        "tool_selection_accuracy",
        "schema_validity",
        "cost_ceiling",
        "latency_ceiling",
        "proposal_match",
    }
)
JUDGE_KEYS = frozenset({"groundedness", "helpfulness"})
GATE_ELIGIBLE_KEYS = DETERMINISTIC_KEYS  # only deterministic scorers gate standalone

# Scorers safe for online sampling of production traces (EVL-FR-021c / AC-9):
# they score the already-emitted trace output and never re-execute the agent or
# re-run SQL against tenant data. sql_result_equivalence is EXCLUDED (it executes
# SQL) so online scoring never re-executes against tenant data.
PRODUCTION_SAFE_SCORERS = frozenset({
    "groundedness", "helpfulness", "schema_validity", "cost_ceiling",
    "latency_ceiling", "tool_selection_accuracy",
})

# scorer_key -> (kind, gate_eligible, version)
SCORER_META: dict[str, dict] = {
    "sql_result_equivalence": {"kind": "deterministic", "gate_eligible": True, "version": 2},
    "tool_selection_accuracy": {"kind": "deterministic", "gate_eligible": True, "version": 1},
    "schema_validity": {"kind": "deterministic", "gate_eligible": True, "version": 1},
    "cost_ceiling": {"kind": "deterministic", "gate_eligible": True, "version": 1},
    "latency_ceiling": {"kind": "deterministic", "gate_eligible": True, "version": 1},
    "proposal_match": {"kind": "deterministic", "gate_eligible": True, "version": 1},
    "groundedness": {"kind": "llm_judge", "gate_eligible": False, "version": 3},
    "helpfulness": {"kind": "llm_judge", "gate_eligible": False, "version": 1},
}


class ScorerRegistry:
    def __init__(self, warehouse=None, judge_client=None):
        self._warehouse = warehouse
        self._judge = judge_client
        self._instances: dict[str, object] = {}
        self._build()

    def _build(self) -> None:
        self._instances = {
            "tool_selection_accuracy": ToolSelectionAccuracyScorer(),
            "schema_validity": SchemaValidityScorer(),
            "cost_ceiling": CostCeilingScorer(),
            "latency_ceiling": LatencyCeilingScorer(),
            "proposal_match": ProposalMatchScorer(),
        }
        if self._warehouse is not None:
            self._instances["sql_result_equivalence"] = SqlResultEquivalenceScorer(self._warehouse)
        if self._judge is not None:
            self._instances["groundedness"] = GroundednessJudgeScorer(self._judge)
            self._instances["helpfulness"] = HelpfulnessJudgeScorer(self._judge)

    def get(self, scorer_key: str):
        scorer = self._instances.get(scorer_key)
        if scorer is None:
            raise KeyError(
                f"scorer {scorer_key!r} not registered (missing warehouse/judge wiring?)"
            )
        return scorer

    def has(self, scorer_key: str) -> bool:
        return scorer_key in self._instances

    @staticmethod
    def is_gate_eligible(scorer_key: str) -> bool:
        return SCORER_META.get(scorer_key, {}).get("gate_eligible", False)

    @staticmethod
    def kind(scorer_key: str) -> str:
        return SCORER_META.get(scorer_key, {}).get("kind", "deterministic")

"""Scorer framework + gate-rule engine (EVL-FR-010..012, BR-1)."""

from __future__ import annotations

import pytest

from app.adapters.fixture_warehouse import DuckDbFixtureWarehouse
from app.domain import gate_rule
from app.domain.gate_rule import GateRuleError
from app.domain.scorers.deterministic import (
    CostCeilingScorer,
    ProposalMatchScorer,
    SchemaValidityScorer,
    SqlResultEquivalenceScorer,
    ToolSelectionAccuracyScorer,
)
from app.domain.scorers.judge import GroundednessJudgeScorer
from tests.conftest import FakeJudgeClient


@pytest.fixture
def warehouse(tmp_path):
    wh = DuckDbFixtureWarehouse(str(tmp_path / "fx"))
    wh.seed(
        "fw",
        {
            "orders": (
                ["region", "net_revenue"],
                [("EMEA", 100.0), ("EMEA", 50.0), ("AMER", 200.0)],
            )
        },
    )
    return wh


async def test_sql_result_equivalence_match(warehouse):
    scorer = SqlResultEquivalenceScorer(warehouse)
    case = {
        "input": {"context_refs": {"fixture_warehouse": "fw"}},
        "expected": {
            "kind": "sql_result",
            "value": {
                "sql": "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region",
                "order_insensitive": True,
                "float_tolerance": 0.01,
            },
        },
    }
    candidate = {"sql": "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"}
    res = await scorer.score(case, candidate, {})
    assert res.passed and res.score == 1.0


async def test_sql_result_equivalence_mismatch_diff(warehouse):
    scorer = SqlResultEquivalenceScorer(warehouse)
    case = {
        "input": {"context_refs": {"fixture_warehouse": "fw"}},
        "expected": {
            "kind": "sql_result",
            "value": {
                "sql": "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region",
                "order_insensitive": True,
            },
        },
    }
    # candidate drops EMEA -> missing group
    candidate = {
        "sql": "SELECT region, SUM(net_revenue) AS rev FROM orders "
        "WHERE region='AMER' GROUP BY region"
    }
    res = await scorer.score(case, candidate, {})
    assert not res.passed and res.score == 0.0
    assert "diff" in res.details


async def test_sql_invalid_candidate_scores_zero(warehouse):
    scorer = SqlResultEquivalenceScorer(warehouse)
    case = {
        "input": {"context_refs": {"fixture_warehouse": "fw"}},
        "expected": {"kind": "sql_result", "value": {"sql": "SELECT 1"}},
    }
    res = await scorer.score(case, {"sql": "SELECT bogus FROM nope"}, {})
    assert not res.passed and res.details["error"] == "candidate_sql_error"


async def test_tool_selection_modes():
    scorer = ToolSelectionAccuracyScorer()
    case = {"expected": {"kind": "tool_sequence", "value": {"tools": ["a", "b"], "mode": "exact"}}}
    assert (await scorer.score(case, {"tools": ["a", "b"]}, {})).passed
    assert not (await scorer.score(case, {"tools": ["a"]}, {})).passed


async def test_schema_validity():
    scorer = SchemaValidityScorer()
    schema = {
        "type": "object",
        "required": ["severity"],
        "properties": {"severity": {"enum": ["low", "high"]}},
    }
    case = {"expected": {"kind": "structured", "value": {"schema": schema}}}
    assert (await scorer.score(case, {"structured": {"severity": "high"}}, {})).passed
    bad = await scorer.score(case, {"structured": {"severity": "nope"}}, {})
    assert not bad.passed and bad.details["errors"]


async def test_cost_ceiling():
    scorer = CostCeilingScorer()
    case = {"expected": {"kind": "sql_result", "value": {}}}
    assert (await scorer.score(case, {"cost_usd": 0.1}, {"usd_per_case_max": 0.25})).passed
    assert not (await scorer.score(case, {"cost_usd": 0.5}, {"usd_per_case_max": 0.25})).passed


async def test_proposal_match_must_fields():
    scorer = ProposalMatchScorer()
    case = {
        "expected": {
            "kind": "proposal",
            "value": {
                "tool": "assign",
                "args": {"team": "B", "severity": "high"},
                "field_weights": {"team": "must", "severity": "should"},
            },
        }
    }
    ok = await scorer.score(
        case, {"proposal": {"tool": "assign", "args": {"team": "B", "severity": "high"}}}, {}
    )
    assert ok.passed
    wrong_team = await scorer.score(
        case, {"proposal": {"tool": "assign", "args": {"team": "A", "severity": "high"}}}, {}
    )
    assert not wrong_team.passed


async def test_judge_scorer_not_gate_eligible_and_parses_rating():
    judge = FakeJudgeClient(rating=4.5)
    scorer = GroundednessJudgeScorer(judge)
    assert scorer.gate_eligible is False  # BR-1
    case = {"input": {"messages": [{"role": "user", "content": "q"}]}, "_tenant_id": "t"}
    res = await scorer.score(case, {"answer": "a", "evidence": ["e"]}, {"pass_threshold": 3.0})
    assert res.score == 4.5 and res.passed
    assert res.details["judge_prompt_ver"] == "groundedness@3"
    assert judge.calls == 1


# ---- gate-rule engine ----


def test_gate_rule_rejects_judge_only():  # AC-3
    with pytest.raises(GateRuleError):
        gate_rule.validate("groundedness.mean >= baseline - 0.3")


def test_gate_rule_accepts_with_deterministic():  # AC-3
    gate_rule.validate(
        "sql_result_equivalence.mean >= baseline - 0.02 AND groundedness.mean >= baseline - 0.3"
    )


def test_gate_rule_rejects_or_mixing_judge_term():  # BR-1 OR-bypass fix
    # An OR rule where a judge term can carry the gate alone is rejected, even
    # though a deterministic term is also present.
    with pytest.raises(GateRuleError):
        gate_rule.validate(
            "sql_result_equivalence.mean >= baseline - 0.02 OR groundedness.mean >= 3.0")


def test_gate_rule_allows_or_of_only_deterministic():
    # OR of deterministic-only terms is allowed (no judge can gate alone).
    gate_rule.validate(
        "sql_result_equivalence.mean >= baseline - 0.02 OR schema_validity.pass_rate >= 0.99")


def test_gate_cannot_pass_when_deterministic_regresses_under_and():  # BR-1
    # AND: a hard-regressed deterministic scorer fails the gate regardless of a
    # perfect judge score.
    expr = "sql_result_equivalence.mean >= baseline - 0.02 AND groundedness.mean >= 3.0"
    agg = {"sql_result_equivalence": {"mean": 0.10}, "groundedness": {"mean": 5.0}}
    base = {"sql_result_equivalence": {"mean": 1.0}}
    passed, _ = gate_rule.evaluate(expr, agg, base)
    assert passed is False


def test_gate_rule_evaluate_pass_and_fail():
    expr = "sql_result_equivalence.mean >= baseline - 0.02 AND cost_ceiling.pass_rate >= 0.98"
    agg = {"sql_result_equivalence": {"mean": 0.93}, "cost_ceiling": {"pass_rate": 0.99}}
    base = {"sql_result_equivalence": {"mean": 0.93}}
    passed, verdicts = gate_rule.evaluate(expr, agg, base)
    assert passed
    # candidate 4% below baseline, threshold 2% -> fail (AC-2)
    agg2 = {"sql_result_equivalence": {"mean": 0.89}, "cost_ceiling": {"pass_rate": 0.99}}
    passed2, verdicts2 = gate_rule.evaluate(expr, agg2, base)
    assert not passed2
    failing = [v for v in verdicts2 if not v.passed][0]
    assert failing.scorer == "sql_result_equivalence"

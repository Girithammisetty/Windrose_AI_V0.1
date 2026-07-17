"""BRD 54 DM-FR-010: the decision-table evaluator — deterministic, first-match,
typed operators, default, explainability. Pure, exhaustive (AC-2)."""

from __future__ import annotations

import pytest

from app.domain.decisions import (
    Condition,
    DecisionModel,
    DecisionModelInvalid,
    Outcome,
    Rule,
    evaluate,
    validate_model,
)


def _model(rules, default=None):
    return DecisionModel(model_id="dm-1", tenant_id="t", name="Reg E table",
                         version=1, rules=rules, default_outcome=default)


FRAUD = Outcome("escalate_fraud_review", "high")
DENY = Outcome("deny_no_error_found", "medium")


def test_first_match_wins():
    m = _model([
        Rule([Condition("dispute_type", "eq", "fraud_unauthorized"),
              Condition("amount", "gt", 1000)], FRAUD, "big fraud"),
        Rule([Condition("dispute_type", "eq", "fraud_unauthorized")], DENY, "any fraud"),
    ])
    ev = evaluate(m, {"dispute_type": "fraud_unauthorized", "amount": "2450.00"})
    assert ev.matched and ev.rule_index == 0
    assert ev.outcome.disposition_code == "escalate_fraud_review"
    assert "rule #0 fired" in ev.explanation and "big fraud" in ev.explanation


def test_numeric_coercion_on_bronze_strings():
    m = _model([Rule([Condition("amount", "gte", 100)], FRAUD)])
    assert evaluate(m, {"amount": "342.20"}).matched is True
    assert evaluate(m, {"amount": "99.99"}).matched is False


@pytest.mark.parametrize("op,val,field,hit", [
    ("eq", "x", "x", True), ("eq", "x", "y", False),
    ("ne", "x", "y", True), ("gt", 5, "6", True), ("gt", 5, "5", False),
    ("gte", 5, "5", True), ("lt", 5, "4", True), ("lte", 5, "5", True),
    ("in", ["a", "b"], "b", True), ("in", ["a", "b"], "c", False),
    ("contains", "raud", "fraud_unauthorized", True),
    ("contains", "zzz", "fraud", False),
])
def test_each_operator(op, val, field, hit):
    m = _model([Rule([Condition("c", op, val)], FRAUD)])
    assert evaluate(m, {"c": field}).matched is hit


def test_exists_operator():
    m = _model([Rule([Condition("note", "exists", True)], FRAUD)])
    assert evaluate(m, {"note": "x"}).matched is True
    assert evaluate(m, {"other": "x"}).matched is False


def test_all_conditions_must_hold():
    m = _model([Rule([Condition("a", "eq", "1"), Condition("b", "eq", "2")], FRAUD)])
    assert evaluate(m, {"a": "1", "b": "2"}).matched is True
    assert evaluate(m, {"a": "1", "b": "9"}).matched is False


def test_default_outcome_and_no_match():
    with_default = _model([Rule([Condition("a", "eq", "1")], FRAUD)], default=DENY)
    ev = evaluate(with_default, {"a": "9"})
    assert ev.matched and ev.rule_index is None
    assert ev.outcome.disposition_code == "deny_no_error_found"
    assert "default" in ev.explanation

    no_default = _model([Rule([Condition("a", "eq", "1")], FRAUD)])
    ev2 = evaluate(no_default, {"a": "9"})
    assert ev2.matched is False and ev2.outcome is None


def test_missing_column_never_matches_comparison():
    m = _model([Rule([Condition("amount", "gt", 100)], FRAUD)])
    assert evaluate(m, {}).matched is False


# ---- validation (DM-FR-040) ----

def test_validate_rejects_bad_outcomes_and_columns():
    with pytest.raises(DecisionModelInvalid, match="at least one rule"):
        validate_model("t", [], None, valid_codes=None, schema_columns=None)

    good = [Rule([Condition("amount", "gt", 100)], FRAUD)]
    # bad severity
    with pytest.raises(DecisionModelInvalid, match="severity"):
        validate_model("t", [Rule([Condition("amount", "gt", 1)],
                                  Outcome("x", "extreme"))],
                       None, valid_codes=None, schema_columns=None)
    # code not in catalog
    with pytest.raises(DecisionModelInvalid, match="catalog"):
        validate_model("t", good, None, valid_codes={"other_code"}, schema_columns=None)
    # column not in schema
    with pytest.raises(DecisionModelInvalid, match="schema"):
        validate_model("t", good, None, valid_codes=None, schema_columns={"other_col"})
    # unknown operator
    with pytest.raises(DecisionModelInvalid, match="operator"):
        validate_model("t", [Rule([Condition("amount", "no_such_op", 1)], FRAUD)],
                       None, valid_codes=None, schema_columns=None)
    # valid passes
    validate_model("t", good, DENY,
                   valid_codes={"escalate_fraud_review", "deny_no_error_found"},
                   schema_columns={"amount"})


# ---- inc2 richer operators (DM-FR-051) --------------------------------------

def test_between_inclusive_numeric():
    m = _model([Rule([Condition("amount", "between", [100, 500])], FRAUD)])
    assert evaluate(m, {"amount": "100"}).matched          # low bound inclusive
    assert evaluate(m, {"amount": 500}).matched            # high bound inclusive
    assert not evaluate(m, {"amount": "99.99"}).matched
    assert not evaluate(m, {"amount": 501}).matched


def test_not_in_and_in():
    incl = _model([Rule([Condition("state", "in", ["CA", "NY"])], FRAUD)])
    assert evaluate(incl, {"state": "ca"}).matched          # case-insensitive
    excl = _model([Rule([Condition("state", "not_in", ["CA", "NY"])], FRAUD)])
    assert evaluate(excl, {"state": "TX"}).matched
    assert not evaluate(excl, {"state": "NY"}).matched


def test_text_shape_operators():
    starts = _model([Rule([Condition("mcc", "starts_with", "59")], FRAUD)])
    assert evaluate(starts, {"mcc": "5999"}).matched
    ends = _model([Rule([Condition("email", "ends_with", "@evil.test")], FRAUD)])
    assert evaluate(ends, {"email": "a@EVIL.test"}).matched
    rx = _model([Rule([Condition("ref", "matches", r"^CB-\d{4}$")], FRAUD)])
    assert evaluate(rx, {"ref": "CB-1234"}).matched
    assert not evaluate(rx, {"ref": "CB-12"}).matched


def test_is_empty_fires_on_missing_or_blank():
    m = _model([Rule([Condition("evidence_url", "is_empty", True)], FRAUD)])
    assert evaluate(m, {}).matched                          # missing column
    assert evaluate(m, {"evidence_url": "  "}).matched      # blank string
    assert not evaluate(m, {"evidence_url": "http://x"}).matched
    # is_empty=false is "present and non-blank"
    present = _model([Rule([Condition("evidence_url", "is_empty", False)], FRAUD)])
    assert present.rules and evaluate(present, {"evidence_url": "http://x"}).matched
    assert not evaluate(present, {}).matched


def test_validate_new_operator_shapes():
    with pytest.raises(DecisionModelInvalid, match="between"):
        validate_model("t", [Rule([Condition("amount", "between", 5)], FRAUD)],
                       None, valid_codes=None, schema_columns=None)
    with pytest.raises(DecisionModelInvalid, match="list"):
        validate_model("t", [Rule([Condition("state", "in", "CA")], FRAUD)],
                       None, valid_codes=None, schema_columns=None)
    with pytest.raises(DecisionModelInvalid, match="regex"):
        validate_model("t", [Rule([Condition("ref", "matches", "(")], FRAUD)],
                       None, valid_codes=None, schema_columns=None)
    # well-formed new operators pass
    validate_model("t", [Rule([Condition("amount", "between", [1, 9])], FRAUD),
                         Rule([Condition("ref", "matches", r"\d+")], DENY)],
                   None, valid_codes=None, schema_columns=None)

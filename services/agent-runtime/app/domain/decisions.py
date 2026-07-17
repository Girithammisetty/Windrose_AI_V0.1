"""Decision-model evaluator (BRD 54) — the deterministic, explainable, no-LLM
decision-table engine. Pure functions only: given a row/case field map and a
tenant's ordered rules, evaluate TOP-DOWN, FIRST match wins, and return the
outcome + which rule fired. Exhaustively unit-tested; the governed
`evaluate -> proposal` wrapper lives in the service layer (reusing
ProposalService), never here.

A Rule matches when ALL its conditions hold. Comparisons coerce numerically when
both sides look numeric (the semantic-layer convention), else compare as strings
case-insensitively — so a table authored over bronze string columns behaves like
the rest of the platform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEVERITIES = ("low", "medium", "high", "critical")
# inc2 richer operators: set/range/text-shape/emptiness/regex on top of inc1's
# scalar comparators. `between` takes a 2-element [lo,hi] (inclusive); `in`/
# `not_in` take a list; `matches` takes a regex; `is_empty` is the inverse of a
# present, non-blank value (so it fires on missing OR "").
OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "between",
             "contains", "starts_with", "ends_with", "matches", "exists",
             "is_empty")


@dataclass(slots=True)
class Condition:
    column: str
    op: str
    value: object = None


@dataclass(slots=True)
class Outcome:
    disposition_code: str
    severity: str


@dataclass(slots=True)
class Rule:
    when: list[Condition]
    then: Outcome
    note: str = ""


@dataclass(slots=True)
class DecisionModel:
    model_id: str
    tenant_id: str
    name: str
    version: int
    rules: list[Rule] = field(default_factory=list)
    default_outcome: Outcome | None = None
    workspace_id: str | None = None
    dataset_urn: str | None = None
    status: str = "draft"
    created_by: str | None = None


@dataclass(slots=True)
class Evaluation:
    matched: bool
    rule_index: int | None
    outcome: Outcome | None
    explanation: str


def _num(v):
    """Coerce to float if it looks numeric, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except (ValueError, AttributeError):
            return None
    return None


def _cmp(left, right) -> int:
    """-1/0/1 comparison: numeric if both coerce, else case-insensitive string."""
    ln, rn = _num(left), _num(right)
    if ln is not None and rn is not None:
        return (ln > rn) - (ln < rn)
    ls, rs = str(left).strip().lower(), str(right).strip().lower()
    return (ls > rs) - (ls < rs)


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _condition_holds(cond: Condition, fields: dict) -> bool:
    present = cond.column in fields and fields[cond.column] is not None
    # Emptiness operators are evaluated BEFORE the present-guard — they are the
    # only ones that are allowed to fire on an absent/blank column.
    if cond.op == "exists":
        return present if _truthy(cond.value, default=True) else not present
    if cond.op == "is_empty":
        want_empty = _truthy(cond.value, default=True)
        empty = not present or _blank(fields.get(cond.column))
        return empty if want_empty else not empty
    if not present:
        return False
    left = fields[cond.column]
    if cond.op == "eq":
        return _cmp(left, cond.value) == 0
    if cond.op == "ne":
        return _cmp(left, cond.value) != 0
    if cond.op == "gt":
        return _cmp(left, cond.value) > 0
    if cond.op == "gte":
        return _cmp(left, cond.value) >= 0
    if cond.op == "lt":
        return _cmp(left, cond.value) < 0
    if cond.op == "lte":
        return _cmp(left, cond.value) <= 0
    if cond.op == "in":
        vals = cond.value if isinstance(cond.value, (list, tuple)) else [cond.value]
        return any(_cmp(left, v) == 0 for v in vals)
    if cond.op == "not_in":
        vals = cond.value if isinstance(cond.value, (list, tuple)) else [cond.value]
        return all(_cmp(left, v) != 0 for v in vals)
    if cond.op == "between":
        lo, hi = (cond.value + [None, None])[:2] if isinstance(cond.value, list) \
            else (None, None)
        return _cmp(left, lo) >= 0 and _cmp(left, hi) <= 0
    if cond.op == "contains":
        return str(cond.value).strip().lower() in str(left).strip().lower()
    if cond.op == "starts_with":
        return str(left).strip().lower().startswith(str(cond.value).strip().lower())
    if cond.op == "ends_with":
        return str(left).strip().lower().endswith(str(cond.value).strip().lower())
    if cond.op == "matches":
        try:
            return re.search(str(cond.value), str(left), re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _truthy(v, *, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def evaluate(model: DecisionModel, fields: dict) -> Evaluation:
    """First-match-wins evaluation with an explicit default (BR-5)."""
    for i, rule in enumerate(model.rules):
        if all(_condition_holds(c, fields) for c in rule.when):
            conds = " AND ".join(
                f"{c.column} {c.op} {c.value}" for c in rule.when) or "(always)"
            note = f" — {rule.note}" if rule.note else ""
            return Evaluation(
                matched=True, rule_index=i, outcome=rule.then,
                explanation=f"rule #{i} fired [{conds}]{note}")
    if model.default_outcome is not None:
        return Evaluation(matched=True, rule_index=None, outcome=model.default_outcome,
                          explanation="no rule matched — applied the default outcome")
    return Evaluation(matched=False, rule_index=None, outcome=None,
                      explanation="no rule matched and no default outcome configured")


# ---- (de)serialization for storage + API (rules/default in jsonb) ----------

def _outcome_to_dict(o: Outcome | None) -> dict | None:
    return None if o is None else {"disposition_code": o.disposition_code,
                                   "severity": o.severity}


def _outcome_from_dict(d: dict | None) -> Outcome | None:
    if not d:
        return None
    return Outcome(disposition_code=str(d.get("disposition_code", "")),
                   severity=str(d.get("severity", "")))


def rules_to_json(rules: list[Rule]) -> list[dict]:
    return [{"when": [{"column": c.column, "op": c.op, "value": c.value} for c in r.when],
             "then": _outcome_to_dict(r.then), "note": r.note} for r in rules]


def rules_from_json(raw) -> list[Rule]:
    out: list[Rule] = []
    for r in raw or []:
        conds = [Condition(column=str(c.get("column", "")), op=str(c.get("op", "")),
                           value=c.get("value")) for c in (r.get("when") or [])]
        out.append(Rule(when=conds, then=_outcome_from_dict(r.get("then")),
                        note=str(r.get("note") or "")))
    return out


def parse_outcome(d: dict | None) -> Outcome | None:
    return _outcome_from_dict(d)


class DecisionModelInvalid(ValueError):
    """Author-time validation failure (DM-FR-040) — carries the offending field."""


def validate_model(name: str, rules: list[Rule], default_outcome: Outcome | None,
                   *, valid_codes: set[str] | None, schema_columns: set[str] | None) -> None:
    """Reject an unauthorable model (DM-FR-040/BR-3/BR-5). ``valid_codes`` =
    the workspace disposition catalog (None → skip that check); ``schema_columns``
    = the dataset schema (None → skip). Fail with the offending field."""
    if not name.strip():
        raise DecisionModelInvalid("name is required")
    if not rules:
        raise DecisionModelInvalid("at least one rule is required")

    def _check_outcome(o: Outcome, where: str) -> None:
        if o.severity not in SEVERITIES:
            raise DecisionModelInvalid(f"{where}: severity {o.severity!r} not in {SEVERITIES}")
        if valid_codes is not None and o.disposition_code not in valid_codes:
            raise DecisionModelInvalid(
                f"{where}: disposition_code {o.disposition_code!r} not in the "
                "workspace catalog")

    for i, rule in enumerate(rules):
        if not rule.when:
            raise DecisionModelInvalid(f"rule #{i}: at least one condition is required")
        for c in rule.when:
            if c.op not in OPERATORS:
                raise DecisionModelInvalid(f"rule #{i}: unknown operator {c.op!r}")
            if c.op == "between" and not (isinstance(c.value, list) and len(c.value) == 2):
                raise DecisionModelInvalid(
                    f"rule #{i}: 'between' needs a [low, high] value")
            if c.op in ("in", "not_in") and not isinstance(c.value, (list, tuple)):
                raise DecisionModelInvalid(
                    f"rule #{i}: {c.op!r} needs a list value")
            if c.op == "matches":
                try:
                    re.compile(str(c.value))
                except re.error as exc:
                    raise DecisionModelInvalid(
                        f"rule #{i}: invalid regex {c.value!r} ({exc})") from exc
            if schema_columns is not None and c.column not in schema_columns:
                raise DecisionModelInvalid(
                    f"rule #{i}: column {c.column!r} not in the dataset schema")
        _check_outcome(rule.then, f"rule #{i}")
    if default_outcome is not None:
        _check_outcome(default_outcome, "default_outcome")

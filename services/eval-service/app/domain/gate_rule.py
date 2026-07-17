"""Gate-rule expression engine (EVL-FR-022/030, BR-1/BR-2).

A gate rule is a boolean AND/OR expression over per-scorer aggregates, e.g.::

    sql_result_equivalence.mean >= baseline - 0.02
      AND cost_ceiling.pass_rate >= 0.98
      AND groundedness.mean >= baseline - 0.3

Each term is ``<scorer>.<aggregate> <op> <rhs>`` where ``<rhs>`` is a literal
number or a ``baseline [±N]`` reference (the baseline's aggregate for the same
scorer). ``validate()`` enforces judge-never-gates-alone (≥1 deterministic term)
and ``evaluate()`` returns per-term verdicts + the overall pass/fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.scorers.registry import GATE_ELIGIBLE_KEYS, JUDGE_KEYS

_TERM_RE = re.compile(
    r"^\s*(?P<scorer>[a-zA-Z0-9_]+)\.(?P<agg>[a-zA-Z0-9_]+)\s*"
    r"(?P<op>>=|<=|==|>|<)\s*(?P<rhs>.+?)\s*$"
)
_BASELINE_RE = re.compile(r"^baseline\s*(?P<sign>[-+])?\s*(?P<num>[0-9.]+)?$")


@dataclass
class Term:
    scorer: str
    aggregate: str
    op: str
    rhs_raw: str
    uses_baseline: bool
    offset: float  # signed offset applied to baseline (0 for literal rhs)
    literal: float | None


@dataclass
class Verdict:
    scorer: str
    aggregate: str
    op: str
    value: float | None
    baseline: float | None
    threshold: float | None
    passed: bool


class GateRuleError(ValueError):
    pass


def _split_top(expr: str, sep: str) -> list[str]:
    return [p for p in re.split(rf"\s+{sep}\s+", expr) if p.strip()]


def parse(expr: str) -> tuple[list[Term], str]:
    """Return (terms, connective). connective is 'AND' or 'OR' (uniform).
    Mixed AND/OR is rejected for determinism (use one connective per rule)."""
    if not expr or not expr.strip():
        raise GateRuleError("empty gate rule")
    connective = "AND"
    parts = _split_top(expr, "AND")
    if len(parts) == 1:
        or_parts = _split_top(expr, "OR")
        if len(or_parts) > 1:
            connective, parts = "OR", or_parts
    else:
        if (
            _split_top(expr, "OR") != [expr]
            and len(_split_top(expr, "OR")) > 1
            and any(" OR " in p for p in parts)
        ):
            raise GateRuleError("mixed AND/OR not supported; use one connective")
    terms = [_parse_term(p) for p in parts]
    return terms, connective


def _parse_term(text: str) -> Term:
    m = _TERM_RE.match(text)
    if not m:
        raise GateRuleError(f"cannot parse gate term: {text!r}")
    rhs = m.group("rhs").strip()
    bm = _BASELINE_RE.match(rhs)
    if bm:
        sign = bm.group("sign") or "+"
        num = float(bm.group("num")) if bm.group("num") else 0.0
        offset = num if sign == "+" else -num
        return Term(m.group("scorer"), m.group("agg"), m.group("op"), rhs, True, offset, None)
    try:
        literal = float(rhs)
    except ValueError as exc:
        raise GateRuleError(f"gate term rhs must be a number or baseline±N: {rhs!r}") from exc
    return Term(m.group("scorer"), m.group("agg"), m.group("op"), rhs, False, 0.0, literal)


def references(expr: str) -> list[tuple[str, str]]:
    """(scorer, aggregate) pairs referenced by the rule."""
    terms, _ = parse(expr)
    return [(t.scorer, t.aggregate) for t in terms]


def validate(expr: str) -> None:
    """BR-1 (judge-never-gates-alone), enforced at suite save AND re-asserted at
    gate evaluation:

    1. The rule MUST reference ≥1 deterministic (gate-eligible) scorer term, and
    2. A judge term must never be able to determine the outcome by itself. Under
       ``AND`` every deterministic term is a mandatory conjunct that must pass, so
       a judge term can only make the gate *stricter* — allowed. Under ``OR`` a
       single passing term carries the whole gate, so a judge term could promote a
       candidate whose deterministic scorer hard-regressed — **rejected**.

    Raises :class:`GateRuleError` otherwise."""
    terms, connective = parse(expr)
    if not any(t.scorer in GATE_ELIGIBLE_KEYS for t in terms):
        raise GateRuleError(
            "gate rule must include at least one deterministic scorer term "
            "(judge-only verdicts never gate alone — BR-1)"
        )
    # An OR rule that includes any judge term lets that judge score carry the gate
    # alone (any() passes if the judge term passes) — forbidden by BR-1.
    if connective == "OR" and any(t.scorer in JUDGE_KEYS for t in terms):
        raise GateRuleError(
            "a judge term may not gate alone: under OR a judge score could carry the "
            "gate by itself while a deterministic scorer has regressed (BR-1). Use AND "
            "so every deterministic term is a mandatory conjunct that must pass."
        )


def _cmp(op: str, a: float, b: float) -> bool:
    return {
        ">=": a >= b,
        "<=": a <= b,
        ">": a > b,
        "<": a < b,
        "==": abs(a - b) < 1e-9,
    }[op]


def evaluate(
    expr: str,
    aggregates: dict[str, dict[str, float]],
    baselines: dict[str, dict[str, float]] | None = None,
) -> tuple[bool, list[Verdict]]:
    """Evaluate the rule. ``aggregates`` and ``baselines`` are
    ``{scorer: {aggregate: value}}``. Missing candidate aggregate -> term fails
    (fail-safe). Missing baseline for a baseline-relative term -> term fails
    (BASELINE handled upstream; here we fail safe)."""
    terms, connective = parse(expr)
    baselines = baselines or {}
    verdicts: list[Verdict] = []
    for t in terms:
        value = aggregates.get(t.scorer, {}).get(t.aggregate)
        if t.uses_baseline:
            base = baselines.get(t.scorer, {}).get(t.aggregate)
            threshold = None if base is None else base + t.offset
        else:
            base = None
            threshold = t.literal
        passed = value is not None and threshold is not None and _cmp(t.op, value, threshold)
        verdicts.append(Verdict(t.scorer, t.aggregate, t.op, value, base, threshold, passed))
    if connective == "OR":
        gate_passed = any(v.passed for v in verdicts)
    else:
        gate_passed = all(v.passed for v in verdicts)
    return gate_passed, verdicts

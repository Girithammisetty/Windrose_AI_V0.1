"""Deterministic scorers v1 (EVL-FR-011). Bit-stable given the same pins
(NFR: same pins -> same deterministic-scorer results).

All scorers here are pure/CPU or read-only-fixture; none can mutate tenant data
(BR-4). ``sql_result_equivalence`` executes both candidate and expected SQL
against the pinned fixture warehouse (a read-only embedded DuckDB eval schema)
and compares result sets order-insensitively with float tolerance and
column-name normalization (BR-9)."""

from __future__ import annotations

from jsonschema import Draft202012Validator

from .base import ScoreResult


def _normcols(cols: list[str]) -> list[str]:
    return [c.strip().lower() for c in cols]


def _round_row(row: tuple, tol: float) -> tuple:
    out = []
    for v in row:
        if isinstance(v, float):
            # Quantize to the tolerance grid so equal-within-tolerance floats hash equal.
            out.append(round(v / tol) * tol if tol > 0 else v)
        else:
            out.append(v)
    return tuple(out)


class SqlResultEquivalenceScorer:
    scorer_key = "sql_result_equivalence"
    version = 2
    kind = "deterministic"
    gate_eligible = True
    applicable_expected_kinds = ("sql_result",)

    def __init__(self, warehouse):
        # warehouse: FixtureWarehouse port with .query(fixture, sql, tenant, ceiling_s)
        self._wh = warehouse

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        expected = case["expected"]["value"]
        fixture = (
            case.get("input", {}).get("context_refs", {}).get("fixture_warehouse")
            or config.get("fixture_warehouse")
            or "default"
        )
        tol = float(expected.get("float_tolerance", config.get("float_tolerance", 0.0)))
        order_insensitive = bool(expected.get("order_insensitive", True))
        candidate_sql = candidate_output.get("sql")
        expected_sql = expected.get("sql")
        if not candidate_sql:
            return ScoreResult(0.0, False, {"error": "candidate produced no SQL"})
        try:
            cand_cols, cand_rows = await self._wh.query(fixture, candidate_sql)
        except TimeoutError:
            # BR-9: a cost-bomb candidate scores 0 with a timeout detail, never hangs.
            return ScoreResult(
                0.0, False, {"error": "timeout", "detail": "candidate SQL timed out"}
            )
        except Exception as exc:  # noqa: BLE001 - invalid candidate SQL fails the case
            return ScoreResult(
                0.0, False, {"error": "candidate_sql_error", "detail": str(exc)[:400]}
            )
        exp_cols, exp_rows = await self._wh.query(fixture, expected_sql)

        if _normcols(cand_cols) != _normcols(exp_cols):
            return ScoreResult(
                0.0,
                False,
                {"diff": f"columns differ: expected {exp_cols}, got {cand_cols}"},
            )
        cand = [_round_row(r, tol) for r in cand_rows]
        exp = [_round_row(r, tol) for r in exp_rows]
        if order_insensitive:
            match = _multiset_equal(cand, exp)
        else:
            match = cand == exp
        if match:
            return ScoreResult(1.0, True, {"rows": len(exp_rows)})
        diff = _row_diff(exp, cand, order_insensitive)
        return ScoreResult(
            0.0,
            False,
            {"diff": diff, "expected_rows": len(exp_rows), "candidate_rows": len(cand_rows)},
        )


def _multiset_equal(a: list, b: list) -> bool:
    from collections import Counter

    return Counter(a) == Counter(b)


def _row_diff(expected: list, candidate: list, order_insensitive: bool) -> str:
    if order_insensitive:
        from collections import Counter

        ce, cc = Counter(expected), Counter(candidate)
        missing = list((ce - cc).elements())
        extra = list((cc - ce).elements())
        parts = []
        if len(expected) != len(candidate):
            parts.append(f"expected {len(expected)} rows, got {len(candidate)}")
        if missing:
            parts.append(f"missing {missing[:3]}")
        if extra:
            parts.append(f"unexpected {extra[:3]}")
        return "; ".join(parts) or "row values differ"
    return f"expected {len(expected)} rows, got {len(candidate)} (order-sensitive mismatch)"


class ToolSelectionAccuracyScorer:
    scorer_key = "tool_selection_accuracy"
    version = 1
    kind = "deterministic"
    gate_eligible = True
    applicable_expected_kinds = ("tool_sequence",)

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        expected = case["expected"]["value"]
        mode = expected.get("mode", config.get("mode", "exact"))
        exp_tools = list(expected.get("tools", []))
        got_tools = list(candidate_output.get("tools", []))
        if mode == "set":
            ok = set(exp_tools) == set(got_tools)
        elif mode == "prefix":
            ok = got_tools[: len(exp_tools)] == exp_tools
        else:  # exact
            ok = got_tools == exp_tools
        score = 1.0 if ok else 0.0
        return ScoreResult(score, ok, {"mode": mode, "expected": exp_tools, "got": got_tools})


class SchemaValidityScorer:
    scorer_key = "schema_validity"
    version = 1
    kind = "deterministic"
    gate_eligible = True
    applicable_expected_kinds = ("structured",)

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        schema = case["expected"]["value"].get("schema") or config.get("schema") or {}
        instance = candidate_output.get("structured", candidate_output)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        if not errors:
            return ScoreResult(1.0, True, {})
        return ScoreResult(
            0.0,
            False,
            {"errors": [{"path": list(e.path), "message": e.message} for e in errors[:5]]},
        )


class CostCeilingScorer:
    scorer_key = "cost_ceiling"
    version = 1
    kind = "deterministic"
    gate_eligible = True
    applicable_expected_kinds = ("sql_result", "tool_sequence", "proposal", "structured", "rubric")

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        ceiling = float(config.get("usd_per_case_max", 1.0))
        cost = float(candidate_output.get("cost_usd", 0.0))
        ok = cost <= ceiling
        return ScoreResult(1.0 if ok else 0.0, ok, {"cost_usd": cost, "ceiling_usd": ceiling})


class LatencyCeilingScorer:
    scorer_key = "latency_ceiling"
    version = 1
    kind = "deterministic"
    gate_eligible = True
    applicable_expected_kinds = ("sql_result", "tool_sequence", "proposal", "structured", "rubric")

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        ceiling = float(config.get("ms_max", 30000))
        latency = float(candidate_output.get("latency_ms", 0))
        ok = latency <= ceiling
        return ScoreResult(
            1.0 if ok else 0.0,
            ok,
            {"latency_ms": latency, "ceiling_ms": ceiling},
            latency_ms=int(latency),
        )


class ProposalMatchScorer:
    """Field-level proposal comparison with per-field must/should weights."""

    scorer_key = "proposal_match"
    version = 1
    kind = "deterministic"
    gate_eligible = True
    applicable_expected_kinds = ("proposal",)

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        expected = case["expected"]["value"]
        exp_tool = expected.get("tool")
        exp_args = expected.get("args", {})
        got = candidate_output.get("proposal", candidate_output)
        got_tool = got.get("tool")
        got_args = got.get("args", {})
        field_weights = expected.get("field_weights") or config.get("field_weights") or {}
        must_fields = {k for k, w in field_weights.items() if w == "must"} or set(exp_args)

        mismatches = []
        if exp_tool is not None and exp_tool != got_tool:
            return ScoreResult(
                0.0, False, {"tool_mismatch": {"expected": exp_tool, "got": got_tool}}
            )

        total_w, got_w = 0.0, 0.0
        for k, exp_v in exp_args.items():
            w = 2.0 if k in must_fields else 1.0
            total_w += w
            if got_args.get(k) == exp_v:
                got_w += w
            else:
                mismatches.append(
                    {
                        "field": k,
                        "expected": exp_v,
                        "got": got_args.get(k),
                        "kind": "must" if k in must_fields else "should",
                    }
                )
        must_ok = all(got_args.get(k) == exp_args.get(k) for k in must_fields if k in exp_args)
        score = (got_w / total_w) if total_w else 1.0
        passed = must_ok and score >= float(config.get("min_score", 1.0 if must_fields else 0.0))
        return ScoreResult(score, passed, {"mismatches": mismatches, "score": score})

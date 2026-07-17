"""Unit: the pushed-down browse SQL builder (semantics mirror the old pandas)."""

from __future__ import annotations

from app.domain.browse_sql import build_order, build_where, is_numeric_type


def test_numeric_types():
    for t in ("BIGINT", "INTEGER", "DOUBLE", "DECIMAL(10,2)", "FLOAT", "HUGEINT"):
        assert is_numeric_type(t)
    for t in ("VARCHAR", "DATE", "BOOLEAN", "BLOB"):
        assert not is_numeric_type(t)


def test_numeric_op_binds_a_float():
    where, params = build_where(
        [{"col": "amount", "op": "gte", "value": "300"}], {"amount": True}
    )
    assert where == '"amount" >= ?'
    assert params == [300.0]


def test_non_numeric_op_on_numeric_col_still_numeric():
    # eq/neq/gt... on a numeric column are numeric comparisons
    where, params = build_where(
        [{"col": "amount", "op": "eq", "value": "250"}], {"amount": True}
    )
    assert where == '"amount" = ?' and params == [250.0]


def test_string_eq_and_contains_are_case_insensitive():
    w1, p1 = build_where([{"col": "s", "op": "eq", "value": "OpEn"}], {"s": False})
    assert w1 == "lower(cast(\"s\" as varchar)) = lower(?)" and p1 == ["OpEn"]
    w2, p2 = build_where([{"col": "s", "op": "contains", "value": "den"}], {"s": False})
    assert "LIKE '%' || lower(?) || '%'" in w2 and p2 == ["den"]


def test_gt_on_a_non_numeric_column_falls_through_to_contains():
    # matches the old pandas fall-through: gt/gte/lt/lte on a string column
    # became a substring match, not a numeric comparison.
    where, _ = build_where([{"col": "s", "op": "gt", "value": "x"}], {"s": False})
    assert "LIKE" in where


def test_like_metacharacters_are_escaped():
    _, params = build_where([{"col": "s", "op": "contains", "value": "a%b_c"}], {"s": False})
    assert params == ["a\\%b\\_c"]


def test_unknown_column_and_empty_value_are_skipped():
    where, params = build_where(
        [
            {"col": "nope", "op": "eq", "value": "x"},   # unknown column
            {"col": "s", "op": "eq", "value": ""},         # empty value
            {"col": "s", "op": "eq", "value": None},       # null value
        ],
        {"s": False},
    )
    assert where == "TRUE" and params == []


def test_multiple_filters_are_anded():
    where, params = build_where(
        [
            {"col": "s", "op": "eq", "value": "open"},
            {"col": "amount", "op": "gte", "value": "150"},
        ],
        {"s": False, "amount": True},
    )
    assert " AND " in where and params == ["open", 150.0]


def test_order_default_and_sorted():
    cols = {"amount", "s"}
    assert build_order(None, "asc", cols) == "ORDER BY __ord ASC"
    assert build_order("amount", "desc", cols) == 'ORDER BY "amount" DESC NULLS LAST, __ord ASC'
    # unknown sort column falls back to file order
    assert build_order("missing", "asc", cols) == "ORDER BY __ord ASC"

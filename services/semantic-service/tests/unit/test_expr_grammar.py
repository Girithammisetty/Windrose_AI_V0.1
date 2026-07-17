"""Restricted expression grammar (SEM-FR-006): allowed shapes parse, everything
outside the grammar -> EXPRESSION_NOT_ALLOWED."""

from __future__ import annotations

import pytest

from app.domain.errors import ExpressionNotAllowed
from app.domain.expr import collect_columns, parse_condition, parse_expression


@pytest.mark.parametrize("expr", [
    "order_total",
    "order_total * 2",
    "order_total - discount",
    "(order_total + discount) / 2",
    "coalesce(discount, 0)",
    "nullif(order_total, 0)",
    "cast(order_total as integer)",
    "date_trunc('month', order_date)",
    "extract(year from order_date)",
    "lower(region)",
    "upper(region)",
    "trim(region)",
    "concat(region, '-', status)",
    "abs(order_total)",
    "round(order_total, 2)",
    "CASE WHEN status = 'completed' THEN order_total ELSE 0 END",
    "CASE WHEN order_total > 100 AND status != 'x' THEN 1 END",
    "CASE WHEN discount IS NULL THEN 0 ELSE discount END",
    "order_total % 10",
    "'it''s quoted'",
    "-1 + order_total",
])
def test_grammar_accepts(expr):
    assert parse_expression(expr) is not None


@pytest.mark.parametrize("expr", [
    "(SELECT 1)",                      # subquery
    "select 1",                        # statement keyword as identifier is uppercase-gated
    "my_udf(order_total)",             # UDF outside whitelist
    "row_number() over ()",            # window fn
    "order_total; DROP TABLE x",       # semicolon
    "order_total -- comment",          # comment
    "order_total /* c */",             # block comment
    'order_total + "col"',             # double quotes
    "order_total + 'unterminated",     # broken string
    "OrderTotal",                      # illegal column casing
    "cast(order_total as blob)",       # cast type outside whitelist
    "extract(epoch from order_date)",  # extract part outside whitelist
    "date_trunc('minute', order_date)",  # grain outside whitelist
    "",                                # empty
    "1 +",                             # dangling operator
    "CASE END",                        # CASE without WHEN
])
def test_grammar_rejects(expr):
    with pytest.raises(ExpressionNotAllowed):
        parse_expression(expr)


def test_condition_grammar():
    ast = parse_condition("status = 'completed' AND (order_total > 10 OR discount IS NULL)")
    assert ast["t"] == "logic" and ast["op"] == "AND"
    with pytest.raises(ExpressionNotAllowed):
        parse_condition("status = 'completed'; DELETE FROM x")
    with pytest.raises(ExpressionNotAllowed):
        parse_condition("status LIKE 'a%'")  # LIKE is a filter op, not grammar


def test_collect_columns_walks_the_whole_ast():
    ast = parse_expression(
        "CASE WHEN status = 'x' THEN coalesce(discount, order_total) ELSE tax END")
    assert collect_columns(ast) == {"status", "discount", "order_total", "tax"}

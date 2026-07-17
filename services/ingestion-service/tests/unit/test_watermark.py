"""Watermark-based incremental query building — bound parameters, never
string interpolation (ING-FR-061, BR-5)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain.errors import ValidationFailedError
from app.domain.watermark import (
    WatermarkSpec,
    build_incremental_query,
    coerce_watermark,
    serialize_watermark,
    validate_spec,
)


def test_v1_wrap_shape_exact() -> None:
    sql, params = build_incremental_query(
        "SELECT * FROM public.orders;",
        WatermarkSpec(column="updated_at", value_type="timestamp", value="2026-07-01T00:00:00Z"),
    )
    assert sql == "SELECT * FROM (SELECT * FROM public.orders) src WHERE updated_at > :watermark"
    assert params == {"watermark": datetime.fromisoformat("2026-07-01T00:00:00+00:00")}


def test_no_literal_splicing() -> None:
    sql, params = build_incremental_query(
        "SELECT * FROM t",
        WatermarkSpec(column="updated_at", value_type="timestamp", value="2026-07-01T00:00:00Z"),
    )
    assert "2026" not in sql
    assert "'" not in sql
    assert ":watermark" in sql
    assert isinstance(params["watermark"], datetime)


@pytest.mark.parametrize(
    ("value_type", "raw", "expected"),
    [
        ("int", "42", 42),
        ("decimal", "10.50", Decimal("10.50")),
        ("timestamp", "2026-07-01T12:30:00Z", datetime.fromisoformat("2026-07-01T12:30:00+00:00")),
        ("date", "2026-07-01", date(2026, 7, 1)),
        ("string", "abc", "abc"),
    ],
)
def test_typed_binding(value_type: str, raw: str, expected: object) -> None:
    """BR-5: watermark values are typed driver parameters."""
    _sql, params = build_incremental_query(
        "SELECT * FROM t", WatermarkSpec(column="wm", value_type=value_type, value=raw)
    )
    assert params["watermark"] == expected
    assert type(params["watermark"]) is type(expected)


@pytest.mark.parametrize("operator", [">", ">=", "<", "<=", "="])
def test_operator_whitelist_accepted(operator: str) -> None:
    sql, _ = build_incremental_query(
        "SELECT 1", WatermarkSpec(column="c", operator=operator, value_type="int", value="1")
    )
    assert f"c {operator} :watermark" in sql


@pytest.mark.parametrize(
    "spec",
    [
        WatermarkSpec(column="up;dated", value_type="int", value="1"),  # injection attempt
        WatermarkSpec(column="a b", value_type="int", value="1"),
        WatermarkSpec(column="1col", value_type="int", value="1"),
        WatermarkSpec(column="c", operator="LIKE", value_type="int", value="1"),
        WatermarkSpec(column="c", operator=">", value_type="uuid", value="1"),
        WatermarkSpec(column="c", value_type="int", value="not-an-int"),
        WatermarkSpec(column="c", value_type="timestamp", value="yesterday"),
    ],
)
def test_invalid_specs_rejected(spec: WatermarkSpec) -> None:
    with pytest.raises(ValidationFailedError):
        validate_spec(spec)


def test_missing_value_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        build_incremental_query("SELECT 1", WatermarkSpec(column="c", value_type="int", value=None))


def test_serialize_roundtrip() -> None:
    for value_type, raw in [
        ("int", "7"),
        ("timestamp", "2026-07-01T00:00:00+00:00"),
        ("date", "2026-07-01"),
    ]:
        coerced = coerce_watermark(value_type, raw)
        assert coerce_watermark(value_type, serialize_watermark(coerced)) == coerced

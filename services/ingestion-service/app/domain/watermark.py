"""Incremental watermark query building (ING-FR-061, BR-5).

The saved statement is wrapped exactly as V1 did —
``SELECT * FROM (<stmt>) src WHERE <col> <op> :watermark`` — but the watermark
is ALWAYS bound as a driver-level parameter. No string interpolation of values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.errors import ValidationFailedError

WATERMARK_VALUE_TYPES: tuple[str, ...] = ("int", "decimal", "timestamp", "date", "string")
WATERMARK_OPERATORS: frozenset[str] = frozenset({">", ">=", "<", "<=", "="})
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class WatermarkSpec:
    column: str
    operator: str = ">"
    value_type: str = "string"
    value: str | None = None  # persisted textual form

    def with_value(self, value: str) -> WatermarkSpec:
        return replace(self, value=value)


def validate_spec(spec: WatermarkSpec) -> None:
    details = []
    if not _IDENT_RE.match(spec.column or ""):
        details.append({"field": "watermark.column", "message": "must be a plain SQL identifier"})
    if spec.operator not in WATERMARK_OPERATORS:
        details.append(
            {
                "field": "watermark.operator",
                "message": f"must be one of {sorted(WATERMARK_OPERATORS)}",
            }
        )
    if spec.value_type not in WATERMARK_VALUE_TYPES:
        details.append(
            {
                "field": "watermark.value_type",
                "message": f"must be one of {list(WATERMARK_VALUE_TYPES)}",
            }
        )
    if not details and spec.value is not None:
        try:
            coerce_watermark(spec.value_type, spec.value)
        except (ValueError, InvalidOperation):
            details.append(
                {"field": "watermark.initial_value", "message": f"not a valid {spec.value_type}"}
            )
    if details:
        raise ValidationFailedError("invalid watermark spec", details=details)


def coerce_watermark(value_type: str, raw: str | int | float) -> Any:
    """Coerce the persisted textual watermark into a typed driver parameter (BR-5)."""
    if value_type == "int":
        return int(raw)
    if value_type == "decimal":
        return Decimal(str(raw))
    if value_type == "timestamp":
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if value_type == "date":
        return date.fromisoformat(str(raw))
    if value_type == "string":
        return str(raw)
    raise ValueError(f"unknown watermark value_type {value_type!r}")


def serialize_watermark(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def build_incremental_query(statement: str, spec: WatermarkSpec) -> tuple[str, dict[str, Any]]:
    """Return (sql, params) with the watermark bound — never spliced (ING-FR-061)."""
    validate_spec(spec)
    if spec.value is None:
        raise ValidationFailedError(
            "watermark value missing",
            details=[
                {"field": "watermark.initial_value", "message": "required for incremental pulls"}
            ],
        )
    inner = statement.strip().rstrip(";")
    sql = f"SELECT * FROM ({inner}) src WHERE {spec.column} {spec.operator} :watermark"
    return sql, {"watermark": coerce_watermark(spec.value_type, spec.value)}

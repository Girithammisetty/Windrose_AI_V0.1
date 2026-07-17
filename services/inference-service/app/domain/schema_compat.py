"""Schema-compatibility validation (INF-FR-002, BR-3).

Compares a model version's input schema (the MLflow signature) against an input
dataset's current-version schema, applying **numeric widening only**
(``int -> long -> float -> double``); ``string`` is never coerced; matching is
**case-sensitive exact**. Every violation is reported (not just the first),
producing the ``compatibility_report`` stored on the job and the ``/validate``
response body.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Numeric widening ladder. A dataset (actual) type is compatible with a model
# (required) type if it is the same or *narrower or equal* on this ladder, i.e.
# the model column can accept the dataset column widened up to the model type.
_NUMERIC_RANK = {"integer": 0, "int": 0, "long": 1, "float": 2, "double": 3}
# canonical display forms
_CANON = {"int": "integer", "integer": "integer", "long": "long", "float": "float",
          "double": "double"}


def _canon(t: str | None) -> str | None:
    if t is None:
        return None
    t = t.strip().lower()
    return _CANON.get(t, t)


def _numeric(t: str | None) -> bool:
    return t in _NUMERIC_RANK


def type_compatible(required: str, actual: str) -> bool:
    """Is a dataset column of ``actual`` type usable where the model wants
    ``required``? Exact match always ok; numeric widening up the ladder ok."""
    r, a = _canon(required), _canon(actual)
    if r == a:
        return True
    if _numeric(r) and _numeric(a):
        # dataset value can be widened to the model type: actual_rank <= required_rank
        return _NUMERIC_RANK[a] <= _NUMERIC_RANK[r]
    return False


@dataclass
class ColumnVerdict:
    name: str
    required_type: str
    actual_type: str | None
    verdict: str  # ok | missing | type_mismatch | nullable_mismatch

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "required_type": self.required_type,
            "actual_type": self.actual_type,
            "verdict": self.verdict,
        }


@dataclass
class CompatibilityReport:
    compatible: bool
    model_stage: str
    columns: list[ColumnVerdict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    row_count: int | None = None

    @property
    def violations(self) -> list[dict]:
        return [c.as_dict() for c in self.columns if c.verdict != "ok"]

    def as_dict(self) -> dict:
        return {
            "compatible": self.compatible,
            "model_stage": self.model_stage,
            "columns": [c.as_dict() for c in self.columns],
            "warnings": self.warnings,
            "row_count": self.row_count,
        }


@dataclass
class ModelInputColumn:
    name: str
    type: str
    required: bool = True  # non-nullable model input when required


def validate_compatibility(
    *,
    model_inputs: list[ModelInputColumn],
    dataset_schema: dict[str, dict],
    model_stage: str,
    row_count: int | None = None,
    model_handles_missing: bool = False,
    allow_empty: bool = False,
) -> CompatibilityReport:
    """Build the compatibility report.

    ``dataset_schema`` maps column name -> ``{"type": str, "nullable": bool}``.
    Every model input column is checked; extra dataset columns are allowed and
    reported as an ``EXTRA_COLUMNS`` warning (INF-FR-002 step 3).
    """
    columns: list[ColumnVerdict] = []
    for col in model_inputs:
        ds_col = dataset_schema.get(col.name)
        if ds_col is None:
            columns.append(ColumnVerdict(col.name, _canon(col.type) or col.type, None, "missing"))
            continue
        actual_type = ds_col.get("type")
        actual_nullable = bool(ds_col.get("nullable", True))
        canon_actual = _canon(actual_type) or actual_type
        if not type_compatible(col.type, actual_type):
            columns.append(
                ColumnVerdict(col.name, _canon(col.type) or col.type, canon_actual, "type_mismatch")
            )
            continue
        # nullable dataset column feeding a required (non-nullable) model column is
        # incompatible unless the model pipeline handles missing values.
        if actual_nullable and col.required and not model_handles_missing:
            columns.append(
                ColumnVerdict(
                    col.name, _canon(col.type) or col.type, canon_actual, "nullable_mismatch"
                )
            )
            continue
        columns.append(
            ColumnVerdict(col.name, _canon(col.type) or col.type, canon_actual, "ok")
        )

    warnings: list[dict] = []
    model_cols = {c.name for c in model_inputs}
    extra = sorted(set(dataset_schema) - model_cols)
    if extra:
        warnings.append({"code": "EXTRA_COLUMNS", "columns": extra})

    compatible = all(c.verdict == "ok" for c in columns)

    if row_count == 0:
        warnings.append({"code": "EMPTY_INPUT", "columns": []})
        if not allow_empty:
            compatible = False

    return CompatibilityReport(
        compatible=compatible,
        model_stage=model_stage,
        columns=columns,
        warnings=warnings,
        row_count=row_count,
    )

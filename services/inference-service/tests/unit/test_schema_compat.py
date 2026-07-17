"""Compatibility validation unit tests (INF-FR-002, BR-3, AC-1, AC-2)."""

from __future__ import annotations

from app.domain.schema_compat import (
    ModelInputColumn,
    type_compatible,
    validate_compatibility,
)


def test_numeric_widening_allowed():
    # dataset int can widen to model long/float/double
    assert type_compatible("long", "integer")
    assert type_compatible("double", "long")
    assert type_compatible("float", "int")
    assert type_compatible("double", "double")


def test_numeric_narrowing_and_string_rejected():
    assert not type_compatible("integer", "double")  # narrowing not allowed
    assert not type_compatible("long", "string")     # string never coerced
    assert not type_compatible("string", "long")
    assert type_compatible("string", "string")


def test_case_sensitive_exact_match():
    inputs = [ModelInputColumn("Amount", "double", required=False)]
    schema = {"amount": {"type": "double", "nullable": False}}  # wrong case
    report = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                    model_stage="production", row_count=5)
    assert report.compatible is False
    assert report.columns[0].verdict == "missing"


def test_ac1_missing_column_rejected():
    inputs = [ModelInputColumn("merchant_id", "string", required=False)]
    schema = {"amount": {"type": "double", "nullable": False}}
    report = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                    model_stage="production", row_count=10)
    assert report.compatible is False
    verdicts = {c.name: c.verdict for c in report.columns}
    assert verdicts["merchant_id"] == "missing"


def test_ac2_all_violations_listed_not_just_first():
    inputs = [
        ModelInputColumn("age", "long", required=False),
        ModelInputColumn("merchant_id", "string", required=False),
    ]
    schema = {"age": {"type": "string", "nullable": False}}  # age mistyped, merchant missing
    report = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                    model_stage="production", row_count=10)
    assert report.compatible is False
    assert len(report.violations) == 2
    verdicts = {c["name"]: c["verdict"] for c in report.violations}
    assert verdicts["age"] == "type_mismatch"
    assert verdicts["merchant_id"] == "missing"


def test_extra_columns_warned_not_blocking():
    inputs = [ModelInputColumn("amount", "double", required=False)]
    schema = {"amount": {"type": "double", "nullable": False},
              "notes": {"type": "string", "nullable": True}}
    report = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                    model_stage="production", row_count=10)
    assert report.compatible is True
    assert any(w["code"] == "EXTRA_COLUMNS" and "notes" in w["columns"]
               for w in report.warnings)


def test_empty_input_warns_and_blocks_unless_allowed():
    inputs = [ModelInputColumn("amount", "double", required=False)]
    schema = {"amount": {"type": "double", "nullable": False}}
    blocked = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                     model_stage="production", row_count=0)
    assert blocked.compatible is False
    allowed = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                     model_stage="production", row_count=0, allow_empty=True)
    assert allowed.compatible is True


def test_nullable_feeding_required_model_column():
    inputs = [ModelInputColumn("amount", "double", required=True)]
    schema = {"amount": {"type": "double", "nullable": True}}
    report = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                    model_stage="production", row_count=5)
    assert report.columns[0].verdict == "nullable_mismatch"
    ok = validate_compatibility(model_inputs=inputs, dataset_schema=schema,
                                model_stage="production", row_count=5,
                                model_handles_missing=True)
    assert ok.compatible is True

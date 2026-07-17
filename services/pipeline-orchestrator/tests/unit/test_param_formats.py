"""Semantic `format` param validation (generic component-config pattern).

`type` is the storage shape; `format` is the semantic meaning that also drives
data-aware validation (column/columns resolved against the input dataset schema).
"""

from __future__ import annotations

from app.domain.params import validate_params


def _run(schema, provided, *, known_columns=None, require_present=True):
    return validate_params(
        "n1", provided, schema, model_type=None,
        require_present=require_present, known_columns=known_columns,
    )


def _codes(items):
    return {i["code"] for i in items}


def test_columns_format_requires_list_of_strings():
    schema = {"cols": {"type": "array", "format": "columns", "required": True}}
    assert _run(schema, {"cols": ["a", "b"]}) == []
    assert "COLUMNS_INVALID" in _codes(_run(schema, {"cols": "a,b"}))
    assert "COLUMNS_INVALID" in _codes(_run(schema, {"cols": [1, 2]}))


def test_columns_format_is_data_aware_when_schema_known():
    schema = {"cols": {"type": "array", "format": "columns", "required": True}}
    known = {"amount", "region", "label"}
    assert _run(schema, {"cols": ["amount", "label"]}, known_columns=known) == []
    bad = _run(schema, {"cols": ["amount", "nope"]}, known_columns=known)
    assert "COLUMN_NOT_FOUND" in _codes(bad)


def test_single_column_format():
    schema = {"on": {"type": "string", "format": "column", "required": True}}
    known = {"id", "policy_id"}
    assert _run(schema, {"on": "policy_id"}, known_columns=known) == []
    assert "COLUMN_NOT_FOUND" in _codes(_run(schema, {"on": "ghost"}, known_columns=known))
    assert "COLUMN_INVALID" in _codes(_run(schema, {"on": ""}, known_columns=known))


def test_expression_format_requires_nonempty_when_required():
    schema = {"expr": {"type": "text", "format": "expression", "required": True}}
    assert _run(schema, {"expr": "amount > 1000"}) == []
    assert "EXPRESSION_INVALID" in _codes(_run(schema, {"expr": "   "}))


def test_dataset_ref_format_validates_urn():
    schema = {"ds": {"type": "string", "format": "dataset_ref", "required": True}}
    assert _run(schema, {"ds": "wr:t1:dataset:dataset/abc"}) == []
    assert "DATASET_REF_INVALID" in _codes(_run(schema, {"ds": "not-a-urn"}))


def test_key_value_format():
    schema = {"tags": {"type": "dictionary", "format": "key_value"}}
    assert _run(schema, {"tags": {"a": "b"}}) == []
    assert "KEY_VALUE_INVALID" in _codes(_run(schema, {"tags": {"a": 1}}))


def test_unknown_format_degrades_to_base_type_only():
    # A format the validator doesn't know yet must not error (forward-compatible).
    schema = {"x": {"type": "string", "format": "future_widget"}}
    assert _run(schema, {"x": "anything"}) == []


def test_known_columns_none_skips_existence_check():
    # Without a resolvable input schema, column format is structural-only.
    schema = {"cols": {"type": "array", "format": "columns"}}
    assert _run(schema, {"cols": ["whatever", "cols"]}, known_columns=None) == []

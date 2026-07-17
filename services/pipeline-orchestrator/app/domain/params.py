"""Component parameter validation against the component.json schema (PIPE-FR-014)."""

from __future__ import annotations

import re

_RESTRICTED = re.compile(r"^[a-zA-Z0-9_\s]*$")

_TYPES = {
    "boolean", "int", "number", "string", "restricted_string", "text", "dictionary",
    "array", "autocomplete", "dataset_column", "anomaly_metric", "dataset_ref",
}

# Semantic ``format`` hints (JSON-Schema `type` + `format` split). ``type`` is the
# storage SHAPE; ``format`` is the MEANING that drives the UI widget, validation,
# and data-binding. New formats can be added here without touching call sites —
# an unknown format degrades to base-type validation only (forward-compatible).
#   - column / columns  : one / a list of column names, resolved against the
#                         node's INPUT dataset schema (data-aware).
#   - dataset_ref       : a dataset URN (wr:{tenant}:dataset:dataset/{id}).
#   - expression        : a code/expression string (non-empty when required).
#   - enum              : a value constrained to `enum` (also enforced generically).
#   - key_value         : a dict[str,str] (dictionary type).
_FORMATS = {"column", "columns", "dataset_ref", "expression", "enum", "key_value", "metric"}


def validate_params(
    alias: str,
    provided: dict,
    schema: dict,
    *,
    model_type: str | None,
    require_present: bool,
    known_columns: set[str] | None = None,
) -> list[dict]:
    """Validate ``provided`` params for one node against the component ``schema``.

    ``require_present`` controls whether required params must be present now (save
    time) or may be deferred as run-time parameters. ``known_columns`` — when the
    node's input dataset schema is resolvable — enables data-aware validation:
    a ``column``/``columns`` param must reference real columns of that dataset.
    Returns a list of report items. Params hidden for the template's model_type
    are skipped (PIPE-FR-014).
    """
    items: list[dict] = []

    def bad(field: str, problem: str, code: str = "PARAM_INVALID") -> None:
        items.append({"alias": alias, "field": f"parameters.{field}", "problem": problem,
                      "code": code})

    # Unknown params rejected.
    for key in provided:
        if key not in schema:
            bad(key, f"unknown parameter {key!r}", "UNKNOWN_PARAM")

    for name, spec in schema.items():
        hide_for = spec.get("hide_for") or []
        if model_type and model_type in hide_for:
            continue
        present = name in provided
        if not present:
            if spec.get("required") and require_present:
                bad(name, "required parameter missing", "MISSING_PARAM")
            continue
        value = provided[name]
        ptype = spec.get("type", "string")
        if ptype in _TYPES:
            _check_type(ptype, name, value, spec, bad)
        # Semantic format checks layer ON TOP of the base-type checks.
        _check_format(name, value, spec, bad, known_columns)
    return items


def _check_format(name, value, spec: dict, bad, known_columns: set[str] | None) -> None:
    """Validate the semantic ``format`` (if any) of a value. Unknown formats are
    ignored so new formats can be declared before validation learns about them."""
    fmt = spec.get("format")
    if not fmt or fmt not in _FORMATS:
        return
    if fmt == "column":
        if not isinstance(value, str) or not value:
            bad(name, "expected a column name", "COLUMN_INVALID")
        elif known_columns is not None and value not in known_columns:
            bad(name, f"column {value!r} is not in the input dataset",
                "COLUMN_NOT_FOUND")
    elif fmt == "columns":
        # Stored as an array of column-name strings (item_format=column).
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            bad(name, "expected a list of column names", "COLUMNS_INVALID")
        elif known_columns is not None:
            missing = [v for v in value if v not in known_columns]
            if missing:
                bad(name, f"columns not in the input dataset: {missing}",
                    "COLUMN_NOT_FOUND")
    elif fmt == "expression":
        if not isinstance(value, str) or (spec.get("required") and not value.strip()):
            bad(name, "expected a non-empty expression", "EXPRESSION_INVALID")
    elif fmt == "dataset_ref":
        if not isinstance(value, str) or not value.startswith("wr:") or ":dataset:" not in value:
            bad(name, "dataset_ref must be a dataset URN "
                "(wr:{tenant}:dataset:dataset/{id})", "DATASET_REF_INVALID")
    elif fmt == "key_value":
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            bad(name, "expected a string-to-string map", "KEY_VALUE_INVALID")


def _check_type(ptype: str, name: str, value, spec: dict, bad) -> None:
    if ptype == "boolean":
        if not isinstance(value, bool):
            bad(name, "expected boolean")
    elif ptype == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            bad(name, "expected integer")
        else:
            _check_numeric(name, value, spec, bad)
    elif ptype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            bad(name, "expected number")
        else:
            _check_numeric(name, value, spec, bad)
    elif ptype in ("string", "text", "autocomplete", "dataset_column", "anomaly_metric"):
        if not isinstance(value, str):
            bad(name, "expected string")
        else:
            _check_string(name, value, spec, bad)
    elif ptype == "restricted_string":
        if not isinstance(value, str) or not _RESTRICTED.match(value):
            bad(name, "restricted_string must match ^[a-zA-Z0-9_\\s]*$",
                "RESTRICTED_STRING")
        else:
            _check_string(name, value, spec, bad)
    elif ptype == "dataset_ref":
        if not isinstance(value, str) or not value:
            bad(name, "dataset_ref must be a non-empty string")
        elif not value.startswith("wr:") or ":dataset:" not in value:
            bad(name, "dataset_ref must be a dataset URN "
                "(wr:{tenant}:dataset:dataset/{id})", "DATASET_REF_INVALID")
    elif ptype == "dictionary":
        if not isinstance(value, dict):
            bad(name, "expected object")
    elif ptype == "array":
        _check_array(name, value, spec, bad)


def _check_numeric(name, value, spec, bad) -> None:
    if "minimum" in spec and value < spec["minimum"]:
        bad(name, f"below minimum {spec['minimum']}")
    if "maximum" in spec and value > spec["maximum"]:
        bad(name, f"above maximum {spec['maximum']}")


def _check_string(name, value, spec, bad) -> None:
    if "enum" in spec and value not in spec["enum"]:
        bad(name, f"not in enum {spec['enum']}", "NOT_IN_ENUM")
    if "min_length" in spec and len(value) < spec["min_length"]:
        bad(name, f"shorter than min_length {spec['min_length']}")
    if "max_length" in spec and len(value) > spec["max_length"]:
        bad(name, f"longer than max_length {spec['max_length']}")


def _check_array(name, value, spec, bad) -> None:
    if not isinstance(value, list):
        bad(name, "expected array")
        return
    if "min_items" in spec and len(value) < spec["min_items"]:
        bad(name, f"fewer than min_items {spec['min_items']}")
    if "max_items" in spec and len(value) > spec["max_items"]:
        bad(name, f"more than max_items {spec['max_items']}")
    if spec.get("unique_items") and len(value) != len({str(v) for v in value}):
        bad(name, "array items must be unique")

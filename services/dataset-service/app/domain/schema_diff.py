"""Schema evolution between consecutive versions (DST-FR-005)."""

from __future__ import annotations


def compute_schema_diff(old: dict, new: dict) -> tuple[dict, bool]:
    """Return (schema_diff, breaking_change).

    Schemas are dicts of column name -> {type, nullable, tags[]}.
    breaking = any removed column or any type change.
    """
    old_cols = {c.lower(): (c, spec) for c, spec in (old or {}).items()}
    new_cols = {c.lower(): (c, spec) for c, spec in (new or {}).items()}

    added = sorted(new_cols[k][0] for k in new_cols.keys() - old_cols.keys())
    removed = sorted(old_cols[k][0] for k in old_cols.keys() - new_cols.keys())
    type_changed = []
    for key in sorted(old_cols.keys() & new_cols.keys()):
        old_type = (old_cols[key][1] or {}).get("type")
        new_type = (new_cols[key][1] or {}).get("type")
        if old_type != new_type:
            type_changed.append({"column": new_cols[key][0], "from": old_type, "to": new_type})

    diff = {"added": added, "removed": removed, "type_changed": type_changed}
    breaking = bool(removed or type_changed)
    return diff, breaking

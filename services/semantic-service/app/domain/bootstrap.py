"""Bootstrap a draft model from V1 chart configs + saved queries (SEM-FR-060..062).

Rules:
- each distinct dataset -> entity; each config.x / dataseries column -> dimension;
  each (y column, aggregateType) pair -> measure named `<agg>_<column>`, deduped
  by expression identity;
- GROUP BY columns and aggregate expressions parsed from saved-query SQL
  contribute likewise;
- passthrough chart types are skipped with a reason;
- idempotent merge (SEM-FR-061): draft items carry origin bootstrap|manual;
  bootstrap only touches its own; same-name/different-expr -> conflict entry,
  kept_existing (BR-12). Output is never auto-published.
"""

from __future__ import annotations

import re

from app.domain.definition import AGG_WHITELIST

_PASSTHROUGH = {"sankey_chart", "grid_chart"}
_AGG_RE = re.compile(
    r"\b(sum|avg|min|max|count)\s*\(\s*(distinct\s+)?(\*|[a-z][a-z0-9_]*)\s*\)",
    re.IGNORECASE,
)
_GROUP_BY_RE = re.compile(r"\bgroup\s+by\s+(.+?)(?:\border\b|\bhaving\b|\blimit\b|$)",
                          re.IGNORECASE | re.DOTALL)
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_TIME_TYPES = {"date", "timestamp", "timestamptz", "datetime"}
_NUMERIC_TYPES = {"int", "integer", "bigint", "long", "double", "float",
                  "decimal", "numeric"}
_DEFAULT_GRAINS = ["day", "week", "month", "quarter", "year"]


def to_prop_name(column: str) -> str:
    """V1 `toPropName`: snake_case -> camelCase (ySeries keys)."""
    head, *rest = column.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _entity_name_for(dataset_urn: str, dataset_name: str | None) -> str:
    base = dataset_name or dataset_urn.rsplit("/", 1)[-1]
    name = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "entity"
    if not name[0].isalpha():
        name = "e_" + name
    return name[:63]


def _dim_type(col_type: str | None) -> tuple[str, list[str]]:
    t = (col_type or "").lower()
    if t in _TIME_TYPES:
        return "time", list(_DEFAULT_GRAINS)
    if t in _NUMERIC_TYPES:
        return "numeric", []
    if t in ("bool", "boolean"):
        return "boolean", []
    return "categorical", []


class BootstrapDeriver:
    def __init__(self, definition: dict, dataset_lookup):
        """`dataset_lookup(dataset_urn) -> {"exists", "table", "schema"} | None`."""
        self.defn = {
            "entities": list(definition.get("entities") or []),
            "dimensions": list(definition.get("dimensions") or []),
            "measures": list(definition.get("measures") or []),
            "join_paths": list(definition.get("join_paths") or []),
        }
        self.lookup = dataset_lookup
        self.created = {"entities": 0, "dimensions": 0, "measures": 0}
        self.examples: list[str] = []
        self.skipped: list[dict] = []
        self.conflicts: list[dict] = []
        self._entity_by_urn = {
            e.get("dataset_urn"): e["name"] for e in self.defn["entities"]
        }

    # -- merge helpers -----------------------------------------------------

    def _ensure_entity(self, source: str, dataset_urn: str,
                       dataset_name: str | None) -> tuple[str, dict] | None:
        if dataset_urn in self._entity_by_urn:
            entity_name = self._entity_by_urn[dataset_urn]
            info = self.lookup(dataset_urn) or {}
            return entity_name, {k.lower(): v for k, v in (info.get("schema") or {}).items()}
        info = self.lookup(dataset_urn)
        if not info or not info.get("exists"):
            self.skipped.append({"source": source,
                                 "reason": f"dataset {dataset_urn!r} not found"})
            return None
        name = _entity_name_for(dataset_urn, dataset_name)
        taken = {e["name"] for e in self.defn["entities"]}
        candidate, n = name, 2
        while candidate in taken:
            candidate, n = f"{name}_{n}", n + 1
        self.defn["entities"].append({
            "name": candidate, "dataset_urn": dataset_urn, "table": info["table"],
            "primary_key": info.get("primary_key") or [],
            "dataset_version_policy": {"policy": "latest"},
            "description": None, "origin": "bootstrap",
        })
        self._entity_by_urn[dataset_urn] = candidate
        self.created["entities"] += 1
        return candidate, {k.lower(): v for k, v in (info.get("schema") or {}).items()}

    def _add_dimension(self, source: str, entity: str, column: str,
                       schema: dict) -> None:
        if not _IDENT_RE.match(column or ""):
            self.skipped.append({"source": source,
                                 "reason": f"illegal column name {column!r}"})
            return
        for d in self.defn["dimensions"]:
            if d["name"] == column:
                if d.get("entity") != entity or (d.get("column") or "") != column:
                    # BR-12: never merge over an existing item; report the conflict
                    self.conflicts.append({
                        "name": column, "kind": "dimension",
                        "existing": {"entity": d.get("entity"), "column": d.get("column")},
                        "candidate": {"entity": entity, "column": column},
                        "action": "kept_existing"})
                return
        if column not in schema:
            self.skipped.append({"source": source,
                                 "reason": f"column {column!r} not in dataset schema"})
            return
        dim_type, grains = _dim_type(str(schema.get(column)))
        self.defn["dimensions"].append({
            "name": column, "entity": entity, "column": column, "type": dim_type,
            "time_grains": grains, "synonyms": [], "origin": "bootstrap",
        })
        self.created["dimensions"] += 1

    def _add_measure(self, source: str, entity: str, agg: str, column: str | None,
                     schema: dict) -> None:
        if agg not in AGG_WHITELIST:
            self.skipped.append({"source": source,
                                 "reason": f"aggregate type {agg!r} not in whitelist"})
            return
        if column is not None and not _IDENT_RE.match(column):
            self.skipped.append({"source": source,
                                 "reason": f"illegal column name {column!r}"})
            return
        if column is not None and column not in schema:
            self.skipped.append({"source": source,
                                 "reason": f"column {column!r} not in dataset schema"})
            return
        name = f"{agg}_{column}" if column else "count_all"
        candidate_expr = f"{agg}({column or '*'})"
        for m in self.defn["measures"]:
            if m["name"] == name:
                existing_expr = f"{m.get('agg')}({m.get('expr') or '*'})"
                same = (m.get("agg") == agg and (m.get("expr") or None) == column
                        and m.get("entity") == entity)
                if not same:
                    self.conflicts.append({
                        "name": name, "kind": "measure",
                        "existing_expr": existing_expr,
                        "candidate_expr": candidate_expr,
                        "action": "kept_existing"})
                return
        # dedup by expression identity across differently-derived sources
        for m in self.defn["measures"]:
            if (m.get("agg") == agg and (m.get("expr") or None) == column
                    and m.get("entity") == entity):
                return
        self.defn["measures"].append({
            "name": name, "entity": entity, "agg": agg, "expr": column,
            "synonyms": [], "origin": "bootstrap",
        })
        self.created["measures"] += 1
        if len(self.examples) < 5:
            self.examples.append(name)

    # -- sources -------------------------------------------------------------

    def add_chart(self, chart: dict) -> None:
        source = f"chart/{chart.get('id', '?')}"
        chart_type = chart.get("chart_type")
        if chart_type in _PASSTHROUGH:
            self.skipped.append({"source": source,
                                 "reason": f"passthrough chart_type {chart_type}"})
            return
        config = chart.get("config") or {}
        meta = chart.get("meta") or {}
        aggregate = meta.get("aggregate") or {}
        default_checked = chart_type != "scatter_plot"
        if not aggregate.get("checked", default_checked):
            self.skipped.append({"source": source,
                                 "reason": "meta.aggregate.checked is false (raw rows)"})
            return
        dataset_urn = chart.get("dataset_urn")
        if not dataset_urn:
            self.skipped.append({"source": source, "reason": "chart has no dataset_urn"})
            return
        resolved = self._ensure_entity(source, dataset_urn, chart.get("dataset_name"))
        if resolved is None:
            return
        entity, schema = resolved

        for dim_col in (config.get("x"), config.get("dataseries")):
            if dim_col:
                self._add_dimension(source, entity, dim_col, schema)

        y = config.get("y")
        y_columns = [y] if isinstance(y, str) else list(y or [])
        y_series = meta.get("ySeries") or {}
        for column in y_columns:
            if chart_type == "pie_chart":
                agg = aggregate.get("type", "sum")
            else:
                series = (y_series.get(to_prop_name(column))
                          or y_series.get(column) or {})
                agg = series.get("aggregateType", "sum")
            self._add_measure(source, entity, agg, column, schema)

    def add_saved_query(self, query: dict) -> None:
        source = f"saved_query/{query.get('id', '?')}"
        sql = query.get("sql") or ""
        dataset_urn = query.get("dataset_urn")
        if not dataset_urn:
            self.skipped.append({"source": source, "reason": "query has no dataset_urn"})
            return
        resolved = self._ensure_entity(source, dataset_urn, query.get("dataset_name"))
        if resolved is None:
            return
        entity, schema = resolved

        for m in _AGG_RE.finditer(sql):
            agg, distinct, column = (m.group(1).lower(), m.group(2),
                                     m.group(3).lower())
            if agg == "count" and distinct:
                agg = "count_distinct"
            self._add_measure(source, entity, agg,
                              None if column == "*" else column, schema)

        group_by = _GROUP_BY_RE.search(sql)
        if group_by:
            for part in group_by.group(1).split(","):
                token = part.strip().lower()
                if _IDENT_RE.match(token):  # skip ordinals/expressions
                    self._add_dimension(source, entity, token, schema)

    def report(self) -> dict:
        return {
            "created": {**self.created, "examples": self.examples},
            "skipped": self.skipped,
            "conflicts": self.conflicts,
        }

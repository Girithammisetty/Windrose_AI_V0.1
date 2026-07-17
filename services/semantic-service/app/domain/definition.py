"""Semantic model definition: schema, authoring validation, diff (SEM-FR-001..009).

The definition is a JSON document stored on each model version. At save time
expressions are parsed to ASTs (SEM-FR-006); at submit time the full validation
suite runs (bindings, references, join graph, limits). The compiler consumes
the parsed `Definition`, never raw JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import expr as ex
from app.domain.errors import ExpressionNotAllowed, LimitExceeded, ValidationFailed
from app.utils import json_size_bytes

AGG_WHITELIST = ("sum", "avg", "min", "max", "count", "count_distinct", "first")
DIM_TYPES = ("categorical", "time", "numeric", "boolean", "geo")
TIME_GRAINS = ("hour", "day", "week", "month", "quarter", "year")
JOIN_TYPES = ("left", "inner")
CARDINALITIES = ("many_to_one", "one_to_one")
NAME_RE = ex.NAME_RE

_NUMERIC_TYPES = {"int", "integer", "bigint", "long", "double", "float", "decimal", "numeric"}
_TIME_TYPES = {"date", "timestamp", "timestamptz", "datetime"}


def _require_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise ValidationFailed(
            f"{what} name must match ^[a-z][a-z0-9_]{{0,62}}$", [{"field": what, "value": value}]
        )
    return value


@dataclass(slots=True)
class Entity:
    name: str
    dataset_urn: str
    table: str  # dotted physical table captured from dataset-service at binding
    primary_key: list[str]
    dataset_version_policy: dict  # {"policy": "latest"} | {"policy": "pinned", "version_no": n}
    description: str | None = None


@dataclass(slots=True)
class Dimension:
    name: str
    entity: str
    dim_type: str
    column: str | None = None
    expr: str | None = None
    expr_ast: dict | None = None
    time_grains: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    description: str | None = None
    deprecated: bool = False
    successor: str | None = None
    origin: str = "manual"  # manual | bootstrap (SEM-FR-061)


@dataclass(slots=True)
class Measure:
    name: str
    entity: str | None = None
    agg: str | None = None
    expr: str | None = None
    expr_ast: dict | None = None
    expr_metric: str | None = None  # derived measure (SEM-FR-004)
    expr_metric_ast: dict | None = None
    filters: str | None = None  # measure-level filter condition
    filters_ast: dict | None = None
    format: str | None = None
    synonyms: list[str] = field(default_factory=list)
    description: str | None = None
    deprecated: bool = False
    successor: str | None = None
    origin: str = "manual"


@dataclass(slots=True)
class JoinPath:
    name: str
    from_entity: str
    to_entity: str
    join_type: str
    on: list[dict]  # [{"from_column":..., "to_column":...}]
    cardinality: str


@dataclass(slots=True)
class Definition:
    entities: dict[str, Entity]
    dimensions: dict[str, Dimension]
    measures: dict[str, Measure]
    join_paths: dict[str, JoinPath]
    raw: dict


def parse_definition(doc: dict, *, settings=None) -> Definition:
    """Structural parse + expression ASTs (save-time validation, SEM-FR-006)."""
    if not isinstance(doc, dict):
        raise ValidationFailed("definition must be a JSON object")
    if settings is not None and json_size_bytes(doc) > settings.definition_max_bytes:
        raise LimitExceeded(
            f"definition exceeds {settings.definition_max_bytes} bytes "
            "(object-storage offload TODO per MASTER-FR-061)"
        )

    entities: dict[str, Entity] = {}
    for e in doc.get("entities", []) or []:
        name = _require_name(e.get("name"), "entity")
        if name in entities:
            raise ValidationFailed(f"duplicate entity name {name!r}")
        pk = e.get("primary_key") or []
        if not isinstance(pk, list) or not all(
            isinstance(c, str) and NAME_RE.match(c) for c in pk
        ):
            raise ValidationFailed(f"entity {name!r}: primary_key must be a list of columns")
        table = e.get("table") or ""
        if not table or not all(
            part and part.replace("_", "a").replace("$", "a").isalnum()
            for part in table.split(".")
        ):
            raise ValidationFailed(f"entity {name!r}: invalid physical table {table!r}")
        policy = e.get("dataset_version_policy") or {"policy": "latest"}
        if policy.get("policy") not in ("latest", "pinned"):
            raise ValidationFailed(f"entity {name!r}: dataset_version_policy must be "
                                   "latest or pinned")
        if policy["policy"] == "pinned" and not isinstance(policy.get("version_no"), int):
            raise ValidationFailed(f"entity {name!r}: pinned policy requires version_no")
        entities[name] = Entity(
            name=name,
            dataset_urn=e.get("dataset_urn") or "",
            table=table,
            primary_key=list(pk),
            dataset_version_policy=policy,
            description=e.get("description"),
        )

    dimensions: dict[str, Dimension] = {}
    for d in doc.get("dimensions", []) or []:
        name = _require_name(d.get("name"), "dimension")
        if name in dimensions:
            raise ValidationFailed(f"duplicate dimension name {name!r}")
        dim_type = d.get("type") or "categorical"
        if dim_type not in DIM_TYPES:
            raise ValidationFailed(
                f"dimension {name!r}: type must be one of {', '.join(DIM_TYPES)}")
        grains = d.get("time_grains") or []
        if dim_type != "time" and grains:
            raise ValidationFailed(f"dimension {name!r}: time_grains only valid on time type")
        if not set(grains) <= set(TIME_GRAINS):
            raise ValidationFailed(
                f"dimension {name!r}: time_grains must be subset of {', '.join(TIME_GRAINS)}")
        column, expr_text = d.get("column"), d.get("expr")
        if bool(column) == bool(expr_text):
            raise ValidationFailed(f"dimension {name!r}: exactly one of column|expr required")
        expr_ast = None
        if column is not None:
            _require_name(column, f"dimension {name!r} column")
        else:
            expr_ast = ex.parse_expression(expr_text)
        dimensions[name] = Dimension(
            name=name,
            entity=_require_name(d.get("entity"), f"dimension {name!r} entity"),
            dim_type=dim_type,
            column=column,
            expr=expr_text,
            expr_ast=expr_ast,
            time_grains=list(grains),
            synonyms=list(d.get("synonyms") or []),
            description=d.get("description"),
            deprecated=bool(d.get("deprecated", False)),
            successor=d.get("successor"),
            origin=d.get("origin", "manual"),
        )

    measures: dict[str, Measure] = {}
    for m in doc.get("measures", []) or []:
        name = _require_name(m.get("name"), "measure")
        if name in measures:
            raise ValidationFailed(f"duplicate measure name {name!r}")  # BR-6
        expr_metric = m.get("expr_metric")
        if expr_metric:
            ast = ex.parse_expression(expr_metric)
            _validate_expr_metric_ast(ast)
            measures[name] = Measure(
                name=name, expr_metric=expr_metric, expr_metric_ast=ast,
                format=m.get("format"), synonyms=list(m.get("synonyms") or []),
                description=m.get("description"),
                deprecated=bool(m.get("deprecated", False)), successor=m.get("successor"),
                origin=m.get("origin", "manual"),
            )
            continue
        agg = m.get("agg")
        if agg not in AGG_WHITELIST:  # AC-4
            raise ValidationFailed(
                f"measure {name!r}: agg {agg!r} not allowed; "
                f"allowed: {', '.join(AGG_WHITELIST)}",
                [{"field": "agg", "allowed": list(AGG_WHITELIST)}],
            )
        expr_text = m.get("expr")
        expr_ast = None
        if expr_text:
            expr_ast = ex.parse_expression(expr_text)
        elif agg != "count":  # BR-3: only count() may omit expr -> count(*)
            raise ValidationFailed(f"measure {name!r}: agg {agg!r} requires an expr")
        filters_text = m.get("filters")
        filters_ast = ex.parse_condition(filters_text) if filters_text else None
        measures[name] = Measure(
            name=name,
            entity=_require_name(m.get("entity"), f"measure {name!r} entity"),
            agg=agg, expr=expr_text, expr_ast=expr_ast,
            filters=filters_text, filters_ast=filters_ast,
            format=m.get("format"), synonyms=list(m.get("synonyms") or []),
            description=m.get("description"),
            deprecated=bool(m.get("deprecated", False)), successor=m.get("successor"),
            origin=m.get("origin", "manual"),
        )

    join_paths: dict[str, JoinPath] = {}
    for j in doc.get("join_paths", []) or []:
        name = _require_name(j.get("name"), "join_path")
        if name in join_paths:
            raise ValidationFailed(f"duplicate join path name {name!r}")
        if j.get("join_type") not in JOIN_TYPES:
            raise ValidationFailed(f"join path {name!r}: join_type must be left|inner")
        cardinality = j.get("cardinality")
        if cardinality not in CARDINALITIES:  # SEM-FR-005: fan-out rejected at authoring
            raise ValidationFailed(
                f"join path {name!r}: cardinality must be many_to_one|one_to_one "
                "(fan-out joins are rejected)")
        on = j.get("on") or []
        if not on or not all(
            isinstance(p, dict) and NAME_RE.match(p.get("from_column") or "")
            and NAME_RE.match(p.get("to_column") or "") for p in on
        ):
            raise ValidationFailed(f"join path {name!r}: on must be "
                                   "[{{from_column, to_column}}]")
        join_paths[name] = JoinPath(
            name=name,
            from_entity=_require_name(j.get("from_entity"), f"join {name!r} from_entity"),
            to_entity=_require_name(j.get("to_entity"), f"join {name!r} to_entity"),
            join_type=j["join_type"], on=list(on), cardinality=cardinality,
        )

    if settings is not None:
        if len(entities) > settings.max_entities:
            raise LimitExceeded(f"more than {settings.max_entities} entities")
        if len(dimensions) > settings.max_dimensions:
            raise LimitExceeded(f"more than {settings.max_dimensions} dimensions")
        if len(measures) > settings.max_measures:
            raise LimitExceeded(f"more than {settings.max_measures} measures")
        if len(join_paths) > settings.max_join_paths:
            raise LimitExceeded(f"more than {settings.max_join_paths} join paths")

    return Definition(entities=entities, dimensions=dimensions, measures=measures,
                      join_paths=join_paths, raw=doc)


def _validate_expr_metric_ast(node: dict) -> None:
    """Derived measures: measure refs combined with + - * / and safe division."""
    t = node.get("t")
    if t == "col":
        return
    if t == "lit" and node.get("kind") == "num":
        return
    if t == "bin" and node.get("op") in ("+", "-", "*", "/"):
        _validate_expr_metric_ast(node["l"])
        _validate_expr_metric_ast(node["r"])
        return
    if t == "func" and node.get("name") == "nullif":
        for arg in node.get("args", []):
            _validate_expr_metric_ast(arg)
        return
    raise ExpressionNotAllowed(
        "derived measures may only combine measures with + - * / and nullif()")


def validate_definition(defn: Definition, dataset_lookup) -> list[dict]:
    """Full validation (submit guard): bindings, references, join graph.

    `dataset_lookup(dataset_urn) -> {"exists": bool, "schema": {col: type}} | None`.
    Returns a list of problems; empty means valid.
    """
    problems: list[dict] = []
    schemas: dict[str, dict] = {}
    all_names: set[str] = set()

    for entity in defn.entities.values():
        info = dataset_lookup(entity.dataset_urn) if entity.dataset_urn else None
        if not info or not info.get("exists"):
            problems.append({"object": f"entity/{entity.name}",
                             "problem": f"dataset {entity.dataset_urn!r} not found"})
            continue
        schema = {k.lower(): str(v).lower() for k, v in (info.get("schema") or {}).items()}
        schemas[entity.name] = schema
        for col in entity.primary_key:
            if col not in schema:
                problems.append({"object": f"entity/{entity.name}",
                                 "problem": f"primary key column {col!r} not in dataset schema"})
        all_names.add(entity.name)

    def check_columns(owner: str, entity_name: str, columns: set[str]) -> None:
        schema = schemas.get(entity_name)
        if schema is None:
            return  # entity problem already recorded
        for col in sorted(columns):
            if col not in schema:
                problems.append({"object": owner,
                                 "problem": f"column {col!r} not in dataset schema "
                                            f"of entity {entity_name!r}"})

    for dim in defn.dimensions.values():
        if dim.entity not in defn.entities:
            problems.append({"object": f"dimension/{dim.name}",
                             "problem": f"unknown entity {dim.entity!r}"})
            continue
        cols = {dim.column} if dim.column else ex.collect_columns(dim.expr_ast)
        check_columns(f"dimension/{dim.name}", dim.entity, cols)
        if dim.dim_type == "time":
            schema = schemas.get(dim.entity, {})
            col_type = schema.get(dim.column or "", "")
            if dim.column and col_type and col_type not in _TIME_TYPES:
                problems.append({"object": f"dimension/{dim.name}",
                                 "problem": f"time dimension must map to a date/timestamp "
                                            f"column, got {col_type!r}"})
        if dim.name in all_names:
            problems.append({"object": f"dimension/{dim.name}", "problem": "name collision"})
        all_names.add(dim.name)

    for meas in defn.measures.values():
        if meas.expr_metric_ast is not None:
            for ref in sorted(ex.collect_columns(meas.expr_metric_ast)):
                if ref not in defn.measures:
                    problems.append({"object": f"measure/{meas.name}",
                                     "problem": f"derived measure references unknown "
                                                f"measure {ref!r}"})
                elif defn.measures[ref].expr_metric is not None and ref == meas.name:
                    problems.append({"object": f"measure/{meas.name}",
                                     "problem": "derived measure self-reference"})
        else:
            if meas.entity not in defn.entities:
                problems.append({"object": f"measure/{meas.name}",
                                 "problem": f"unknown entity {meas.entity!r}"})
            else:
                cols = ex.collect_columns(meas.expr_ast) | ex.collect_columns(meas.filters_ast)
                check_columns(f"measure/{meas.name}", meas.entity, cols)
                # BR-3: avg of a non-numeric column rejected at authoring
                if meas.agg == "avg" and meas.expr_ast and meas.expr_ast.get("t") == "col":
                    col_type = schemas.get(meas.entity, {}).get(meas.expr_ast["name"], "")
                    # dataset-service's profiler emits "decimal(p,s)" (precision/
                    # scale), not the bare "decimal" this whitelist checks —
                    # match by prefix so profiled decimal columns aren't
                    # rejected as non-numeric.
                    is_numeric = col_type in _NUMERIC_TYPES or col_type.startswith("decimal")
                    if col_type and not is_numeric:
                        problems.append({"object": f"measure/{meas.name}",
                                         "problem": f"avg of non-numeric column "
                                                    f"({col_type})"})
            if meas.agg == "count_distinct" and meas.expr_ast is None:
                problems.append({"object": f"measure/{meas.name}",
                                 "problem": "count_distinct requires a column/expr"})
        if meas.name in all_names:
            problems.append({"object": f"measure/{meas.name}", "problem": "name collision"})
        all_names.add(meas.name)

    # BR-6: synonyms must not collide with names of other objects
    for obj_kind, items in (("dimension", defn.dimensions), ("measure", defn.measures)):
        for item in items.values():
            for syn in item.synonyms:
                if syn in all_names and syn != item.name:
                    problems.append({"object": f"{obj_kind}/{item.name}",
                                     "problem": f"synonym {syn!r} collides with another "
                                                "object name"})

    # Join graph: endpoints exist, columns exist, undirected graph acyclic
    edges: list[tuple[str, str]] = []
    for jp in defn.join_paths.values():
        for endpoint in (jp.from_entity, jp.to_entity):
            if endpoint not in defn.entities:
                problems.append({"object": f"join_path/{jp.name}",
                                 "problem": f"unknown entity {endpoint!r}"})
        if jp.from_entity in defn.entities and jp.to_entity in defn.entities:
            check_columns(f"join_path/{jp.name}", jp.from_entity,
                          {p["from_column"] for p in jp.on})
            check_columns(f"join_path/{jp.name}", jp.to_entity,
                          {p["to_column"] for p in jp.on})
            edges.append((jp.from_entity, jp.to_entity))

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    # Parallel paths between the same entity pair are allowed (AC-9: compile
    # resolves the ambiguity via pinning); only true cycles are rejected.
    for a, b in sorted({(min(a, b), max(a, b)) for a, b in edges}):
        ra, rb = find(a), find(b)
        if ra == rb:
            problems.append({"object": "join_paths",
                             "problem": f"join graph cycle involving {a!r} and {b!r}"})
        else:
            parent[ra] = rb

    return problems


def compute_diff(old: dict | None, new: dict) -> dict:
    """Machine-readable diff for model.version_published (SEM-FR-007)."""
    diff: dict = {"added": {}, "removed": {}, "changed": {}}
    old = old or {}
    for kind in ("entities", "dimensions", "measures", "join_paths"):
        old_items = {i["name"]: i for i in (old.get(kind) or [])}
        new_items = {i["name"]: i for i in (new.get(kind) or [])}
        added = sorted(set(new_items) - set(old_items))
        removed = sorted(set(old_items) - set(new_items))
        changed = sorted(
            name for name in set(new_items) & set(old_items)
            if old_items[name] != new_items[name]
        )
        if added:
            diff["added"][kind] = added
        if removed:
            diff["removed"][kind] = removed
        if changed:
            diff["changed"][kind] = changed
    return diff


def broken_refs_for_schema_change(
    defn: Definition, dataset_urn: str, removed: set[str], retyped: set[str]
) -> list[dict]:
    """SEM-FR-008: which measures/dimensions break when dataset columns change."""
    affected = {c.lower() for c in removed} | {c.lower() for c in retyped}
    bound_entities = {e.name for e in defn.entities.values()
                      if e.dataset_urn == dataset_urn}
    if not bound_entities or not affected:
        return []
    broken: list[dict] = []
    for entity_name in sorted(bound_entities):
        entity = defn.entities[entity_name]
        pk_hit = sorted(set(c.lower() for c in entity.primary_key) & affected)
        if pk_hit:
            broken.append({"object_type": "entity", "name": entity_name,
                           "columns": pk_hit, "reason": "primary key column changed"})
    for dim in defn.dimensions.values():
        if dim.entity not in bound_entities:
            continue
        cols = {dim.column} if dim.column else ex.collect_columns(dim.expr_ast)
        hit = sorted({c.lower() for c in cols} & affected)
        if hit:
            broken.append({"object_type": "dimension", "name": dim.name,
                           "columns": hit, "reason": "column removed or retyped"})
    for meas in defn.measures.values():
        if meas.entity not in bound_entities:
            continue
        cols = ex.collect_columns(meas.expr_ast) | ex.collect_columns(meas.filters_ast)
        hit = sorted({c.lower() for c in cols} & affected)
        if hit:
            broken.append({"object_type": "measure", "name": meas.name,
                           "columns": hit, "reason": "column removed or retyped"})
    return broken

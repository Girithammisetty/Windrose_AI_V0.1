"""The metric compiler: (metrics + dimensions + filters + time grain) -> safe SQL.

Safety rules (SEM-FR-022), enforced by construction:
(a) aggregation functions come only from the whitelist — measures are
    dereferenced by name from the model, never accepted as SQL;
(b) every identifier is quoted per dialect and must resolve to a model-declared
    object (column allowlist);
(c) filter values are NEVER interpolated — they exit as `?` placeholders with a
    typed params array;
(d) filter operators come from a fixed whitelist; LIKE patterns are parameters;
(e) all name-ish request fields are gated by ^[a-z][a-z0-9_]{0,62}$ before any
    model lookup;
(f) limit capped, dimension/metric counts capped.

Determinism (BR-7): dimensions in request order, metrics in request order,
filters sorted by (dimension, op, values), GROUP BY ordinals, params in order of
first appearance, single-space single-line SQL. Same request + model version +
dialect => byte-identical SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.compiler.dialects import Dialect, get_dialect
from app.compiler.timeutil import resolve_relative
from app.domain.definition import Definition, Dimension, Entity, Measure
from app.domain.errors import (
    AmbiguousJoinPath,
    LimitExceeded,
    ModelUnhealthy,
    UnknownDimension,
    UnknownGrain,
    UnknownMetric,
    ValidationFailed,
)
from app.domain.expr import NAME_RE, TIME_GRAINS, collect_columns
from app.utils import canonical_json, sha256_hex

FILTER_OPS = ("=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "BETWEEN", "LIKE",
              "IS NULL", "IS NOT NULL")
HAVING_OPS = ("=", "!=", ">", ">=", "<", "<=")


@dataclass(slots=True)
class DimRef:
    name: str
    grain: str | None = None


@dataclass(slots=True)
class FilterSpec:
    dimension: str
    op: str
    values: list


@dataclass(slots=True)
class HavingSpec:
    metric: str
    op: str
    value: object


@dataclass(slots=True)
class OrderSpec:
    name: str
    desc: bool = False


@dataclass(slots=True)
class TimeRange:
    dimension: str
    start: str | None = None
    end: str | None = None
    relative: str | None = None


@dataclass(slots=True)
class CompileRequest:
    metrics: list[str]
    dimensions: list[DimRef] = field(default_factory=list)
    filters: list[FilterSpec] = field(default_factory=list)
    time_range: TimeRange | None = None
    order_by: list[OrderSpec] = field(default_factory=list)
    limit: int | None = None
    having: list[HavingSpec] = field(default_factory=list)
    join_paths: list[str] = field(default_factory=list)
    order_within_group: str | None = None

    def canonical(self) -> dict:
        return {
            "metrics": self.metrics,
            "dimensions": [{"name": d.name, "grain": d.grain} for d in self.dimensions],
            "filters": sorted(
                ({"dimension": f.dimension, "op": f.op, "values": f.values}
                 for f in self.filters),
                key=lambda f: (f["dimension"], f["op"], canonical_json(f["values"])),
            ),
            "time_range": (
                {"dimension": self.time_range.dimension, "start": self.time_range.start,
                 "end": self.time_range.end, "relative": self.time_range.relative}
                if self.time_range else None
            ),
            "order_by": [{"name": o.name, "desc": o.desc} for o in self.order_by],
            "limit": self.limit,
            "having": [{"metric": h.metric, "op": h.op, "value": h.value}
                       for h in self.having],
            "join_paths": self.join_paths,
            "order_within_group": self.order_within_group,
        }

    def request_hash(self) -> str:
        return sha256_hex(canonical_json(self.canonical()))


@dataclass(slots=True)
class Compiled:
    sql: str
    params: list[dict]
    dialect: str
    output_schema: list[dict]
    measures: list[str]
    dimensions: list[str]
    join_paths: list[str]
    time_range_resolved: dict | None
    warnings: list[str]


# ---------------------------------------------------------------------------
# Request normalization (safety rule e — gate before any model lookup, AC-3)


def _gate(name, exc_cls, what: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise exc_cls(f"{what} {name!r} does not match ^[a-z][a-z0-9_]{{0,62}}$")
    return name


def _scalar(value, where: str):
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    raise ValidationFailed(f"{where}: filter values must be scalars")


def normalize_request(body: dict, settings) -> CompileRequest:
    """Parse + regex-gate a compile request body. No model access here."""
    metrics_in = body.get("metrics") or []
    if not isinstance(metrics_in, list) or not metrics_in:
        raise ValidationFailed("metrics: at least one metric required")
    metrics: list[str] = []
    for m in metrics_in:
        name = _gate(m, UnknownMetric, "metric")
        if name not in metrics:
            metrics.append(name)
    if len(metrics) > settings.compile_max_metrics:
        raise LimitExceeded(f"more than {settings.compile_max_metrics} metrics")

    dims: list[DimRef] = []
    seen_dims: set[str] = set()
    for d in body.get("dimensions") or []:
        if isinstance(d, str):
            d = {"name": d}
        name = _gate(d.get("name"), UnknownDimension, "dimension")
        grain = d.get("grain")
        if grain is not None and grain not in TIME_GRAINS:
            raise UnknownGrain(f"grain {grain!r}; allowed: {', '.join(TIME_GRAINS)}")
        if name not in seen_dims:
            seen_dims.add(name)
            dims.append(DimRef(name=name, grain=grain))
    if len(dims) > settings.compile_max_dimensions:
        raise LimitExceeded(f"more than {settings.compile_max_dimensions} dimensions")

    filters: list[FilterSpec] = []
    for f in body.get("filters") or []:
        dimension = _gate(f.get("dimension"), UnknownDimension, "filter dimension")
        op = f.get("op")
        if op not in FILTER_OPS:
            raise ValidationFailed(
                f"filter op {op!r} not allowed; allowed: {', '.join(FILTER_OPS)}")
        values = f.get("values")
        if values is None:
            values = []
        if not isinstance(values, list):
            values = [values]
        values = [_scalar(v, f"filter on {dimension}") for v in values]
        if op in ("IS NULL", "IS NOT NULL") and values:
            raise ValidationFailed(f"filter {op} takes no values")
        if op == "BETWEEN" and len(values) != 2:
            raise ValidationFailed("filter BETWEEN takes exactly two values")
        if op in ("IN", "NOT IN") and not values:
            raise ValidationFailed(f"filter {op} requires at least one value")
        if op in ("=", "!=", ">", ">=", "<", "<=", "LIKE") and len(values) != 1:
            raise ValidationFailed(f"filter {op} takes exactly one value")
        if op == "LIKE" and not isinstance(values[0], str):
            raise ValidationFailed("LIKE pattern must be a string (bound as a parameter)")
        filters.append(FilterSpec(dimension=dimension, op=op, values=values))

    time_range = None
    tr = body.get("time_range")
    if tr:
        time_range = TimeRange(
            dimension=_gate(tr.get("dimension"), UnknownDimension, "time_range dimension"),
            start=tr.get("start"), end=tr.get("end"), relative=tr.get("relative"),
        )
        if time_range.relative is None and not (time_range.start and time_range.end):
            raise ValidationFailed("time_range requires relative or start+end")

    order_by: list[OrderSpec] = []
    for o in body.get("order_by") or []:
        if isinstance(o, str):
            desc = o.startswith("-")
            o = {"name": o.lstrip("-"), "desc": desc}
        order_by.append(OrderSpec(
            name=_gate(o.get("name"), ValidationFailed, "order_by"),
            desc=bool(o.get("desc", False)),
        ))

    having: list[HavingSpec] = []
    for h in body.get("having") or []:
        op = h.get("op")
        if op not in HAVING_OPS:
            raise ValidationFailed(
                f"having op {op!r} not allowed; allowed: {', '.join(HAVING_OPS)}")
        having.append(HavingSpec(
            metric=_gate(h.get("metric"), UnknownMetric, "having metric"),
            op=op, value=_scalar(h.get("value"), "having"),
        ))

    limit = body.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            raise ValidationFailed("limit must be a positive integer")
        if limit > settings.compile_limit_cap:
            raise LimitExceeded(f"limit capped at {settings.compile_limit_cap}")

    join_paths = [
        _gate(j, ValidationFailed, "join_path") for j in (body.get("join_paths") or [])
    ]
    owg = body.get("order_within_group")
    if owg is not None:
        owg = _gate(owg, ValidationFailed, "order_within_group")

    return CompileRequest(
        metrics=metrics, dimensions=dims, filters=filters, time_range=time_range,
        order_by=order_by, limit=limit, having=having, join_paths=join_paths,
        order_within_group=owg,
    )


# ---------------------------------------------------------------------------
# Rendering helpers


class _Params:
    """Ordered bind parameters. Placeholders are emitted as $1..$n — the
    positional-binds contract query-service's sqlsafe.ScanBinds enforces for
    /sql/run with `binds` (`?` is explicitly rejected there, QRY-FR-002)."""

    def __init__(self):
        self.items: list[dict] = []

    def bind(self, value, ptype: str) -> str:
        self.items.append({"type": ptype, "value": value})
        return f"${len(self.items)}"


def _ptype(value, dim: Dimension | None = None) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if dim is not None and dim.dim_type == "time" and isinstance(value, str):
        return "date" if len(value) == 10 else "timestamp"
    return "string"


def _render_literal(node: dict) -> str:
    kind = node["kind"]
    if kind == "num":
        return str(node["v"])
    if kind == "str":
        return "'" + str(node["v"]).replace("'", "''") + "'"
    if kind == "bool":
        return node["v"].upper()
    return "NULL"


def render_ast(node: dict, dialect: Dialect, resolve_col) -> str:
    """Render a grammar AST; `resolve_col(name) -> quoted SQL` (allowlisted)."""
    t = node["t"]
    if t == "col":
        return resolve_col(node["name"])
    if t == "lit":
        return _render_literal(node)
    if t == "bin":
        left = render_ast(node["l"], dialect, resolve_col)
        right = render_ast(node["r"], dialect, resolve_col)
        return f"({left} {node['op']} {right})"
    if t == "func":
        args = ", ".join(render_ast(a, dialect, resolve_col) for a in node["args"])
        return f"{node['name']}({args})"
    if t == "cast":
        return f"CAST({render_ast(node['expr'], dialect, resolve_col)} AS {node['type']})"
    if t == "extract":
        return f"extract({node['part']} FROM {render_ast(node['expr'], dialect, resolve_col)})"
    if t == "date_trunc":
        return dialect.date_trunc(node["grain"],
                                  render_ast(node["expr"], dialect, resolve_col))
    if t == "case":
        parts = ["CASE"]
        for w in node["whens"]:
            parts.append(f"WHEN {render_ast(w['when'], dialect, resolve_col)} "
                         f"THEN {render_ast(w['then'], dialect, resolve_col)}")
        if node.get("else") is not None:
            parts.append(f"ELSE {render_ast(node['else'], dialect, resolve_col)}")
        parts.append("END")
        return " ".join(parts)
    if t == "cond":
        left = render_ast(node["l"], dialect, resolve_col)
        if node["op"] in ("IS NULL", "IS NOT NULL"):
            return f"{left} {node['op']}"
        return f"{left} {node['op']} {render_ast(node['r'], dialect, resolve_col)}"
    if t == "logic":
        left = render_ast(node["l"], dialect, resolve_col)
        right = render_ast(node["r"], dialect, resolve_col)
        return f"({left} {node['op']} {right})"
    if t == "not":
        return f"(NOT {render_ast(node['c'], dialect, resolve_col)})"
    raise ValidationFailed(f"unrenderable AST node {t!r}")


# ---------------------------------------------------------------------------
# The compiler


class Compiler:
    def __init__(self, defn: Definition, *, model_version_label: str,
                 broken_names: set[str] | None = None, settings=None,
                 now: datetime | None = None, timezone: str = "UTC"):
        self.defn = defn
        self.version_label = model_version_label
        self.broken = broken_names or set()
        self.settings = settings
        self.now = now
        self.timezone = timezone

    # -- resolution -------------------------------------------------------

    def _measure(self, name: str, warnings: list[str]) -> Measure:
        measure = self.defn.measures.get(name)
        if measure is None:
            raise UnknownMetric(f"unknown metric {name!r}")
        if name in self.broken:
            raise ModelUnhealthy(
                f"measure {name!r} references broken columns",
                [{"name": name, "reason": "broken_ref"}])
        if measure.deprecated:
            suffix = f" (successor: {measure.successor})" if measure.successor else ""
            warning = f"DEPRECATED: measure {name}{suffix}"
            if warning not in warnings:
                warnings.append(warning)
        return measure

    def _dimension(self, name: str, warnings: list[str]) -> Dimension:
        dim = self.defn.dimensions.get(name)
        if dim is None:
            raise UnknownDimension(f"unknown dimension {name!r}")
        if name in self.broken:
            raise ModelUnhealthy(
                f"dimension {name!r} references broken columns",
                [{"name": name, "reason": "broken_ref"}])
        if dim.deprecated:
            suffix = f" (successor: {dim.successor})" if dim.successor else ""
            warning = f"DEPRECATED: dimension {name}{suffix}"
            if warning not in warnings:
                warnings.append(warning)
        return dim

    def _base_measures(self, name: str, warnings: list[str],
                       seen: tuple = ()) -> list[str]:
        """Expand a (possibly derived) measure to its base measures, in order."""
        if name in seen:
            raise ValidationFailed(f"derived measure cycle at {name!r}")
        measure = self._measure(name, warnings)
        if measure.expr_metric_ast is None:
            return [name]
        out: list[str] = []
        for ref in sorted(collect_columns(measure.expr_metric_ast)):
            for base in self._base_measures(ref, warnings, (*seen, name)):
                if base not in out:
                    out.append(base)
        if not out:
            raise ValidationFailed(f"derived measure {name!r} references no measures")
        return out

    # -- join resolution ----------------------------------------------------

    def _paths_between(self, source: str, target: str,
                       pinned: list[str]) -> list[list[str]]:
        """All simple directed paths source->target over declared join paths."""
        edges: dict[str, list] = {}
        for jp in self.defn.join_paths.values():
            if pinned and jp.name not in pinned:
                continue
            edges.setdefault(jp.from_entity, []).append(jp)
        results: list[list[str]] = []

        def dfs(at: str, visited: tuple, names: tuple):
            if at == target:
                results.append(list(names))
                return
            for jp in edges.get(at, []):
                if jp.to_entity not in visited:
                    dfs(jp.to_entity, (*visited, jp.to_entity), (*names, jp.name))

        dfs(source, (source,), ())
        return results

    def _resolve_joins(self, base_entity: str, targets: list[str],
                       pinned: list[str]) -> list[str]:
        """Ordered, de-duplicated join-path hop names for all target entities."""
        hops: list[str] = []
        for target in targets:
            if target == base_entity:
                continue
            paths = self._paths_between(base_entity, target, pinned)
            if not paths:
                raise ValidationFailed(
                    f"no declared join path from {base_entity!r} to {target!r} "
                    "(joins are never inferred)")
            if len(paths) > 1:  # BR-4 / AC-9
                raise AmbiguousJoinPath(
                    f"multiple join paths from {base_entity!r} to {target!r}; "
                    "pin one via join_paths",
                    [{"candidates": paths}])
            for hop in paths[0]:
                if hop not in hops:
                    hops.append(hop)
        return hops

    # -- SELECT-scope rendering ---------------------------------------------

    def _aliases(self, base_entity: str, hops: list[str]) -> dict[str, str]:
        order = [base_entity]
        for hop in hops:
            jp = self.defn.join_paths[hop]
            for name in (jp.from_entity, jp.to_entity):
                if name not in order:
                    order.append(name)
        aliases: dict[str, str] = {}
        used: set[str] = set()
        for name in order:
            alias = name[0]
            n = 2
            while alias in used:
                alias = f"{name[0]}{n}"
                n += 1
            used.add(alias)
            aliases[name] = alias
        return aliases

    def _col(self, dialect: Dialect, aliases: dict[str, str], entity: str,
             column: str) -> str:
        alias = aliases.get(entity)
        if alias is None:
            raise ValidationFailed(f"entity {entity!r} not joined in this scope")
        return f"{dialect.quote(alias)}.{dialect.quote(column)}"

    def _dim_sql(self, dim: Dimension, grain: str | None, dialect: Dialect,
                 aliases: dict[str, str]) -> str:
        if grain is not None:
            if dim.dim_type != "time":
                raise UnknownGrain(f"dimension {dim.name!r} is not a time dimension")
            if grain not in dim.time_grains:
                raise UnknownGrain(
                    f"dimension {dim.name!r} does not offer grain {grain!r}; "
                    f"available: {', '.join(dim.time_grains) or 'none'}")

        def resolve(col: str) -> str:
            return self._col(dialect, aliases, dim.entity, col)

        inner = (resolve(dim.column) if dim.column
                 else render_ast(dim.expr_ast, dialect, resolve))
        return dialect.date_trunc(grain, inner) if grain else inner

    def _measure_sql(self, measure: Measure, dialect: Dialect,
                     aliases: dict[str, str], order_within_group: str | None) -> str:
        def resolve(col: str) -> str:
            return self._col(dialect, aliases, measure.entity, col)

        expr_sql = (render_ast(measure.expr_ast, dialect, resolve)
                    if measure.expr_ast is not None else None)
        if measure.filters_ast is not None:
            cond_sql = render_ast(measure.filters_ast, dialect, resolve)
            expr_sql = (f"CASE WHEN {cond_sql} THEN {expr_sql} END"
                        if expr_sql is not None
                        else f"CASE WHEN {cond_sql} THEN 1 END")
        agg = measure.agg
        if agg == "count":
            return f"count({expr_sql})" if expr_sql is not None else "count(*)"
        if expr_sql is None:
            raise ValidationFailed(f"measure {measure.name!r}: agg {agg!r} requires an expr")
        if agg == "count_distinct":
            return dialect.count_distinct(expr_sql)
        if agg == "first":
            entity = self.defn.entities[measure.entity]
            order_col = order_within_group or (
                entity.primary_key[0] if entity.primary_key else None)
            if order_col is None:  # BR-8
                raise ValidationFailed(
                    f"measure {measure.name!r}: 'first' needs order_within_group or an "
                    "entity primary key (arbitrary() is never emitted)")
            return dialect.first(expr_sql, resolve(order_col))
        return f"{agg}({expr_sql})"

    def _derived_sql(self, measure: Measure, dialect: Dialect, resolve_metric) -> str:
        return render_ast(measure.expr_metric_ast, dialect, resolve_metric)

    def _where_sql(self, req: CompileRequest, dialect: Dialect,
                   aliases: dict[str, str], params: _Params,
                   warnings: list[str]) -> tuple[str | None, dict | None]:
        clauses: list[str] = []
        ordered = sorted(
            req.filters,
            key=lambda f: (f.dimension, f.op, canonical_json(f.values)))
        for f in ordered:
            dim = self._dimension(f.dimension, warnings)
            target = self._dim_sql(dim, None, dialect, aliases)
            if f.op in ("IS NULL", "IS NOT NULL"):
                clauses.append(f"{target} {f.op}")
            elif f.op in ("IN", "NOT IN"):
                phs = ", ".join(params.bind(v, _ptype(v, dim)) for v in f.values)
                clauses.append(f"{target} {f.op} ({phs})")
            elif f.op == "BETWEEN":
                lo = params.bind(f.values[0], _ptype(f.values[0], dim))
                hi = params.bind(f.values[1], _ptype(f.values[1], dim))
                clauses.append(f"{target} BETWEEN {lo} AND {hi}")
            else:
                ph = params.bind(f.values[0], _ptype(f.values[0], dim))
                clauses.append(f"{target} {f.op} {ph}")

        resolved_range: dict | None = None
        if req.time_range is not None:
            dim = self._dimension(req.time_range.dimension, warnings)
            if dim.dim_type != "time":
                raise ValidationFailed(
                    f"time_range dimension {dim.name!r} is not a time dimension")
            if req.time_range.relative:
                start, end = resolve_relative(
                    req.time_range.relative, self.now or datetime.now(),  # noqa: DTZ005
                    self.timezone)
                start, end = start.isoformat(), end.isoformat()
            else:
                start, end = req.time_range.start, req.time_range.end
            target = self._dim_sql(dim, None, dialect, aliases)
            lo = params.bind(start, "date" if len(str(start)) == 10 else "timestamp")
            hi = params.bind(end, "date" if len(str(end)) == 10 else "timestamp")
            clauses.append(f"{target} >= {lo} AND {target} < {hi}")
            resolved_range = {"dimension": dim.name, "start": start, "end": end,
                              "timezone": self.timezone}
        return (" AND ".join(clauses) if clauses else None), resolved_range

    def _from_sql(self, base_entity: str, hops: list[str], dialect: Dialect,
                  aliases: dict[str, str]) -> str:
        entity = self.defn.entities[base_entity]
        parts = [f"{self._dataset_ref(entity)} {dialect.quote(aliases[base_entity])}"]
        for hop in hops:
            jp = self.defn.join_paths[hop]
            to_entity = self.defn.entities[jp.to_entity]
            kw = "LEFT JOIN" if jp.join_type == "left" else "INNER JOIN"
            conds = " AND ".join(
                f"{self._col(dialect, aliases, jp.from_entity, p['from_column'])} = "
                f"{self._col(dialect, aliases, jp.to_entity, p['to_column'])}"
                for p in jp.on
            )
            parts.append(f"{kw} {self._dataset_ref(to_entity)} "
                         f"{dialect.quote(aliases[jp.to_entity])} ON {conds}")
        return " ".join(parts)

    @staticmethod
    def _dataset_ref(entity: Entity) -> str:
        """A ``{{dataset('name'[, version=N])}}`` macro reference (QRY-FR-005)
        instead of a raw physical table. query-service's tenant-namespace guard
        (BR-2) only allows resolved dataset macros — a literal
        ``FROM "main"."tbl"`` is rejected as outside the tenant's namespaces,
        because the guard's allowlist is built ONLY from macros it resolves
        itself; it never trusts a bare qualified identifier.

        The macro name is the slug segment of ``entity.table``
        (``physical_table`` is always ``<namespace>.<safe_relation(dataset.name)>``,
        captured at binding time — SEM-FR-002). dataset-service's
        ``/datasets/resolve`` endpoint that backs the macro matches it via its
        normalized-relation fallback even when the dataset's real name differs
        from the slug (e.g. contains hyphens) — see dataset-service
        ``DatasetService.resolve()``, which computes
        ``safe_relation(candidate.name) == safe_relation(name)`` for exactly
        this reason.
        """
        slug = entity.table.rsplit(".", 1)[-1]
        policy = entity.dataset_version_policy or {}
        if policy.get("policy") == "pinned" and policy.get("version_no"):
            return "{{dataset('%s', version=%d)}}" % (slug, int(policy["version_no"]))
        return "{{dataset('%s')}}" % slug

    # -- public entry ---------------------------------------------------------

    def compile(self, req: CompileRequest, dialect_name: str) -> Compiled:
        dialect = get_dialect(dialect_name)
        warnings: list[str] = []

        # Resolve everything up front (fail fast, before SQL assembly)
        requested: list[Measure] = [self._measure(m, warnings) for m in req.metrics]
        base_order: list[str] = []
        for name in req.metrics:
            for base in self._base_measures(name, warnings):
                if base not in base_order:
                    base_order.append(base)
        dims: list[tuple[Dimension, str | None]] = [
            (self._dimension(d.name, warnings), d.grain) for d in req.dimensions
        ]

        # Group base measures by entity, order of first appearance
        groups: list[tuple[str, list[str]]] = []
        for base in base_order:
            entity = self.defn.measures[base].entity
            for g_entity, names in groups:
                if g_entity == entity:
                    names.append(base)
                    break
            else:
                groups.append((entity, [base]))

        params = _Params()
        if len(groups) == 1:
            sql, resolved_range, join_hops = self._compile_single(
                req, dialect, groups[0][0], params, warnings)
        else:
            sql, resolved_range, join_hops = self._compile_multi(
                req, dialect, groups, params, warnings)

        return Compiled(
            sql=sql,
            params=params.items,
            dialect=dialect_name,
            output_schema=self._output_schema(req, dims, requested),
            measures=list(req.metrics),
            dimensions=[d.name for d in req.dimensions],
            join_paths=join_hops,
            time_range_resolved=resolved_range,
            warnings=warnings,
        )

    # -- single-entity plan ---------------------------------------------------

    def _select_items(self, req: CompileRequest, dialect: Dialect,
                      aliases: dict[str, str], warnings: list[str]) -> list[str]:
        items: list[str] = []
        for d in req.dimensions:
            dim = self._dimension(d.name, warnings)
            items.append(f"{self._dim_sql(dim, d.grain, dialect, aliases)} "
                         f"AS {dialect.quote(d.name)}")
        for name in req.metrics:
            measure = self.defn.measures[name]
            if measure.expr_metric_ast is not None:
                def resolve_metric(ref: str) -> str:
                    ref_measure = self.defn.measures[ref]
                    if ref_measure.expr_metric_ast is not None:
                        return self._derived_sql(ref_measure, dialect, resolve_metric)
                    return self._measure_sql(ref_measure, dialect, aliases,
                                             req.order_within_group)
                items.append(f"{self._derived_sql(measure, dialect, resolve_metric)} "
                             f"AS {dialect.quote(name)}")
            else:
                measure_sql = self._measure_sql(measure, dialect, aliases,
                                                req.order_within_group)
                items.append(f"{measure_sql} AS {dialect.quote(name)}")
        return items

    def _order_limit(self, req: CompileRequest, dialect: Dialect,
                     warnings: list[str]) -> tuple[str, str, str]:
        """Returns (select_prefix, order_clause, limit_clause)."""
        select_names = [d.name for d in req.dimensions] + list(req.metrics)
        order_parts: list[str] = []
        if req.order_by:
            for o in req.order_by:
                if o.name not in select_names:
                    raise ValidationFailed(
                        f"order_by {o.name!r} is not a selected dimension or metric")
                ordinal = select_names.index(o.name) + 1
                order_parts.append(f"{ordinal} DESC" if o.desc else str(ordinal))
        else:
            # Deterministic default: time dimensions order the result (BRD §5 example)
            for i, d in enumerate(req.dimensions):
                if self._dimension(d.name, warnings).dim_type == "time":
                    order_parts.append(str(i + 1))
        order_clause = f" ORDER BY {', '.join(order_parts)}" if order_parts else ""

        select_prefix, limit_clause = "", ""
        if req.limit is not None:
            if dialect.name == "synapse":
                select_prefix = f"TOP {req.limit} "
            else:
                limit_clause = f" LIMIT {req.limit}"
        return select_prefix, order_clause, limit_clause

    def _having_sql(self, req: CompileRequest, dialect: Dialect,
                    aliases: dict[str, str], params: _Params) -> str:
        if not req.having:
            return ""
        clauses = []
        for h in req.having:
            measure = self.defn.measures.get(h.metric)
            if measure is None:
                raise UnknownMetric(f"unknown having metric {h.metric!r}")
            if measure.expr_metric_ast is not None:
                def resolve_metric(ref: str) -> str:
                    return self._measure_sql(self.defn.measures[ref], dialect, aliases,
                                             req.order_within_group)
                target = self._derived_sql(measure, dialect, resolve_metric)
            else:
                target = self._measure_sql(measure, dialect, aliases,
                                           req.order_within_group)
            ph = params.bind(h.value, _ptype(h.value))
            clauses.append(f"{target} {h.op} {ph}")
        return " HAVING " + " AND ".join(clauses)

    def _scope_targets(self, req: CompileRequest, warnings: list[str]) -> list[str]:
        """Entities needed by dims/filters/time_range, in appearance order."""
        targets: list[str] = []
        for d in req.dimensions:
            entity = self._dimension(d.name, warnings).entity
            if entity not in targets:
                targets.append(entity)
        for f in sorted(req.filters,
                        key=lambda f: (f.dimension, f.op, canonical_json(f.values))):
            entity = self._dimension(f.dimension, warnings).entity
            if entity not in targets:
                targets.append(entity)
        if req.time_range is not None:
            entity = self._dimension(req.time_range.dimension, warnings).entity
            if entity not in targets:
                targets.append(entity)
        return targets

    def _group_by(self, req: CompileRequest, dialect: Dialect,
                  aliases: dict[str, str], warnings: list[str]) -> str:
        if not req.dimensions:
            return ""
        if dialect.group_by_ordinals:
            keys = [str(i + 1) for i in range(len(req.dimensions))]
        else:  # T-SQL: repeat the dimension expressions
            keys = [
                self._dim_sql(self._dimension(d.name, warnings), d.grain, dialect,
                              aliases)
                for d in req.dimensions
            ]
        return " GROUP BY " + ", ".join(keys)

    def _compile_single(self, req: CompileRequest, dialect: Dialect, base_entity: str,
                        params: _Params, warnings: list[str]):
        hops = self._resolve_joins(base_entity, self._scope_targets(req, warnings),
                                   req.join_paths)
        aliases = self._aliases(base_entity, hops)
        items = self._select_items(req, dialect, aliases, warnings)
        where_sql, resolved_range = self._where_sql(req, dialect, aliases, params, warnings)
        group_by = self._group_by(req, dialect, aliases, warnings)
        having = self._having_sql(req, dialect, aliases, params)
        select_prefix, order_clause, limit_clause = self._order_limit(req, dialect, warnings)
        sql = (f"SELECT {select_prefix}{', '.join(items)} "
               f"FROM {self._from_sql(base_entity, hops, dialect, aliases)}"
               + (f" WHERE {where_sql}" if where_sql else "")
               + group_by + having + order_clause + limit_clause)
        return sql, resolved_range, hops

    # -- multi-entity plan (SEM-FR-021: joined CTEs on shared dimensions) ------

    def _compile_multi(self, req: CompileRequest, dialect: Dialect,
                       groups: list[tuple[str, list[str]]], params: _Params,
                       warnings: list[str]):
        ctes: list[str] = []
        all_hops: list[str] = []
        resolved_range: dict | None = None
        cte_of_measure: dict[str, str] = {}
        for idx, (entity, names) in enumerate(groups):
            cte_name = f"m{idx}"
            hops = self._resolve_joins(entity, self._scope_targets(req, warnings),
                                       req.join_paths)
            for hop in hops:
                if hop not in all_hops:
                    all_hops.append(hop)
            aliases = self._aliases(entity, hops)
            items: list[str] = []
            for d in req.dimensions:
                dim = self._dimension(d.name, warnings)
                items.append(f"{self._dim_sql(dim, d.grain, dialect, aliases)} "
                             f"AS {dialect.quote(d.name)}")
            for name in names:
                measure = self.defn.measures[name]
                items.append(
                    f"{self._measure_sql(measure, dialect, aliases, req.order_within_group)} "
                    f"AS {dialect.quote(name)}")
                cte_of_measure[name] = cte_name
            where_sql, resolved_range = self._where_sql(req, dialect, aliases, params,
                                                        warnings)
            group_by = self._group_by(req, dialect, aliases, warnings)
            ctes.append(
                f"{dialect.quote(cte_name)} AS (SELECT {', '.join(items)} "
                f"FROM {self._from_sql(entity, hops, dialect, aliases)}"
                + (f" WHERE {where_sql}" if where_sql else "") + group_by + ")")

        q = dialect.quote
        outer_items: list[str] = []
        cte_names = [f"m{i}" for i in range(len(groups))]
        for d in req.dimensions:
            refs = [f"{q(c)}.{q(d.name)}" for c in cte_names]
            expr = refs[0] if len(refs) == 1 else f"coalesce({', '.join(refs)})"
            outer_items.append(f"{expr} AS {q(d.name)}")

        def resolve_metric(ref: str) -> str:
            ref_measure = self.defn.measures[ref]
            if ref_measure.expr_metric_ast is not None:
                return self._derived_sql(ref_measure, dialect, resolve_metric)
            return f"{q(cte_of_measure[ref])}.{q(ref)}"

        for name in req.metrics:
            measure = self.defn.measures[name]
            if measure.expr_metric_ast is not None:
                outer_items.append(
                    f"{self._derived_sql(measure, dialect, resolve_metric)} AS {q(name)}")
            else:
                outer_items.append(f"{q(cte_of_measure[name])}.{q(name)} AS {q(name)}")

        from_parts = [q(cte_names[0])]
        for k in range(1, len(cte_names)):
            if req.dimensions:
                conds = []
                for d in req.dimensions:
                    prior = [f"{q(c)}.{q(d.name)}" for c in cte_names[:k]]
                    prior_expr = prior[0] if len(prior) == 1 else \
                        f"coalesce({', '.join(prior)})"
                    conds.append(f"{q(cte_names[k])}.{q(d.name)} = {prior_expr}")
                from_parts.append(f"FULL JOIN {q(cte_names[k])} ON " + " AND ".join(conds))
            else:
                from_parts.append(f"CROSS JOIN {q(cte_names[k])}")

        # HAVING semantics land as outer WHERE (outer scope is not grouped)
        outer_where = ""
        if req.having:
            clauses = []
            for h in req.having:
                measure = self.defn.measures.get(h.metric)
                if measure is None:
                    raise UnknownMetric(f"unknown having metric {h.metric!r}")
                target = (self._derived_sql(measure, dialect, resolve_metric)
                          if measure.expr_metric_ast is not None
                          else resolve_metric(h.metric))
                ph = params.bind(h.value, _ptype(h.value))
                clauses.append(f"{target} {h.op} {ph}")
            outer_where = " WHERE " + " AND ".join(clauses)

        select_prefix, order_clause, limit_clause = self._order_limit(req, dialect, warnings)
        sql = (f"WITH {', '.join(ctes)} SELECT {select_prefix}{', '.join(outer_items)} "
               f"FROM {' '.join(from_parts)}" + outer_where + order_clause + limit_clause)
        return sql, resolved_range, all_hops

    # -- output schema ---------------------------------------------------------

    def _output_schema(self, req: CompileRequest,
                       dims: list[tuple[Dimension, str | None]],
                       measures: list[Measure]) -> list[dict]:
        out: list[dict] = []
        for dim, grain in dims:
            if dim.dim_type == "time":
                dtype = "timestamp" if grain in (None, "hour") else "date"
            else:
                dtype = {"categorical": "string", "numeric": "numeric",
                         "boolean": "boolean", "geo": "string"}[dim.dim_type]
            out.append({"name": dim.name, "type": dtype, "role": "dimension"})
        for measure in measures:
            if measure.agg in ("count", "count_distinct"):
                mtype = "bigint"
            elif measure.agg == "first":
                mtype = measure.format or "string"
            else:
                mtype = measure.format or "decimal"
            out.append({"name": measure.name, "type": mtype, "role": "measure"})
        return out

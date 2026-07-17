"""Typed-DAG validation (PIPE-FR-010..016). Pure functions over the definition JSON
and the component catalog; no I/O. Produces a structured validation_report whose
items are stable for the API error envelope and the ACs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.entities import Component
from app.domain.enums import (
    NON_RUNNABLE_TYPES,
    PORT_TYPES,
    PipelineType,
)
from app.domain.params import validate_params
from app.domain.resources import resolve_resources

_ALIAS_RE = re.compile(r"^[A-Za-z0-9]+([-._ ]+[A-Za-z0-9]+)*$")


@dataclass
class ValidationReport:
    valid: bool = True
    items: list[dict] = field(default_factory=list)
    effective_resources: dict = field(default_factory=dict)

    def add(self, code: str, problem: str, *, alias: str | None = None,
            field_: str | None = None) -> None:
        self.valid = False
        self.items.append({"code": code, "alias": alias, "field": field_,
                           "problem": problem})

    def to_dict(self) -> dict:
        return {"status": "valid" if self.valid else "draft", "items": self.items}


def _edge_endpoint(ref: str) -> tuple[str, str]:
    """Split ``alias.port`` -> (alias, port). The alias may itself contain dots, so
    split on the last dot."""
    alias, _, port = ref.rpartition(".")
    if not alias:  # no dot present
        return ref, ""
    return alias, port


def _output_type(node: dict, comp: Component | None, port: str) -> str | None:
    outs = node.get("outputs") or (comp.definition.get("outputs") if comp else None) or []
    for o in outs:
        if o.get("name") == port:
            return o.get("type")
    # single-output components: accept the default port
    if len(outs) == 1 and port in ("", outs[0].get("name")):
        return outs[0].get("type")
    return None


def _input_type(comp: Component | None) -> str:
    if comp is None:
        return "dataframe"
    return comp.definition.get("input_type", "dataframe")


def validate_definition(
    definition: dict,
    *,
    pipeline_type: PipelineType,
    model_type: str | None,
    components: dict[str, Component],
    quota_ceiling: dict,
    mode: str = "all",
    known_columns: set[str] | None = None,
) -> ValidationReport:
    # ``known_columns``: when the caller has resolved the pipeline's source
    # dataset schema, column/columns params are validated to reference real
    # columns (data-aware). None => structural-only (the default; the UI already
    # constrains column pickers to the real schema).
    report = ValidationReport()
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []

    # PIPE-FR-010: non-empty DAG.
    if not nodes:
        report.add("EMPTY_DAG", "pipeline definition has no nodes")
        return report

    # Alias syntax + uniqueness.
    by_alias: dict[str, dict] = {}
    for node in nodes:
        alias = node.get("alias", "")
        if not _ALIAS_RE.match(alias or ""):
            report.add("INVALID_ALIAS", f"alias {alias!r} does not match naming rule",
                       alias=alias or None)
        if alias in by_alias:
            report.add("DUPLICATE_ALIAS", f"alias {alias!r} is not unique", alias=alias)
        by_alias[alias] = node

    # Component existence + arity metadata (PIPE-FR-012, BR-11).
    for alias, node in by_alias.items():
        cname = node.get("component")
        comp = components.get(cname)
        if comp is None:
            report.add("COMPONENT_NOT_AVAILABLE",
                       f"component {cname!r} not in tenant catalog", alias=alias)
        elif not comp.enabled:
            report.add("COMPONENT_NOT_AVAILABLE",
                       f"component {cname!r} disabled for tenant", alias=alias)

    # Edge references must resolve (no dangling); collect (from_alias, to_alias).
    topo_edges: list[tuple[str, str]] = []
    in_counts: dict[str, int] = dict.fromkeys(by_alias, 0)
    out_counts: dict[str, int] = dict.fromkeys(by_alias, 0)
    for i, edge in enumerate(edges):
        fa, fp = _edge_endpoint(edge.get("from", ""))
        ta, _tp = _edge_endpoint(edge.get("to", ""))
        if fa not in by_alias:
            report.add("DANGLING_EDGE", f"edge[{i}] from unknown alias {fa!r}",
                       field_=f"edges[{i}]")
            continue
        if ta not in by_alias:
            report.add("DANGLING_EDGE", f"edge[{i}] to unknown alias {ta!r}",
                       field_=f"edges[{i}]")
            continue
        topo_edges.append((fa, ta))
        in_counts[ta] += 1
        out_counts[fa] += 1
        # PIPE-FR-011: type-compatible edges.
        prod_type = _output_type(by_alias[fa], components.get(by_alias[fa].get("component")),
                                 fp)
        cons_type = _input_type(components.get(by_alias[ta].get("component")))
        declared = edge.get("type")
        if declared and declared not in PORT_TYPES:
            report.add("EDGE_TYPE_MISMATCH", f"edge[{i}] unknown port type {declared!r}",
                       field_=f"edges[{i}]")
        if prod_type and cons_type and prod_type != cons_type:
            report.add(
                "EDGE_TYPE_MISMATCH",
                f"{fa}.{fp}({prod_type}) -> {ta}({cons_type})",
                field_=f"edges[{i}]")

    # Acyclicity (PIPE-FR-010, AC-1) — report the cycle's aliases.
    cycle = _find_cycle(by_alias, topo_edges)
    if cycle:
        report.add("DAG_CYCLE", f"cycle detected: {cycle}", field_="edges")
        report.items[-1]["cycle"] = cycle

    # Arity (PIPE-FR-012).
    for alias, node in by_alias.items():
        comp = components.get(node.get("component"))
        if comp is None:
            continue
        d = comp.definition
        if not (d["min_inputs"] <= in_counts[alias] <= d["max_inputs"]):
            report.add("ARITY_VIOLATION",
                       f"{in_counts[alias]} inputs not in "
                       f"[{d['min_inputs']},{d['max_inputs']}]", alias=alias)
        declared_out = len(node.get("outputs") or d.get("outputs") or [])
        if declared_out > d["max_outputs"]:
            report.add("ARITY_VIOLATION",
                       f"{declared_out} outputs exceed max {d['max_outputs']}",
                       alias=alias)

    # Parameter validation (PIPE-FR-014).
    require_present = mode == "all"
    for alias, node in by_alias.items():
        comp = components.get(node.get("component"))
        if comp is None:
            continue
        schema = comp.definition.get("parameters", {})
        for item in validate_params(alias, node.get("parameters") or {}, schema,
                                    model_type=model_type,
                                    require_present=require_present,
                                    known_columns=known_columns):
            report.valid = False
            report.items.append(item)

    # Terminal-node + read-component rules (PIPE-FR-013).
    _validate_terminals(report, by_alias, out_counts, pipeline_type)

    # Resource validation + inheritance (PIPE-FR-016, AC-11).
    try:
        report.effective_resources = resolve_resources(
            by_alias, topo_edges, quota_ceiling)
    except Exception:  # noqa: BLE001 — bad graph already reported above
        report.effective_resources = {}

    return report


def _find_cycle(nodes: dict[str, dict], edges: list[tuple[str, str]]) -> list[str] | None:
    from collections import defaultdict

    adj: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(nodes, WHITE)
    stack: list[str] = []

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        stack.append(u)
        for v in adj[u]:
            if color.get(v) == GRAY:
                idx = stack.index(v)
                return stack[idx:]
            if color.get(v) == WHITE:
                found = dfs(v)
                if found:
                    return found
        stack.pop()
        color[u] = BLACK
        return None

    for node in nodes:
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


_WRITE = {"write-to-warehouse"}
_BATCH_WRITE = {"batch-write-to-warehouse"}
_READ = {"read-from-warehouse"}
_BATCH_READ = {"batch-read-from-warehouse"}


def _validate_terminals(report, by_alias, out_counts, pipeline_type) -> None:
    comps = {a: n.get("component") for a, n in by_alias.items()}
    present = set(comps.values())
    terminals = [a for a in by_alias if out_counts[a] == 0]

    if pipeline_type in NON_RUNNABLE_TYPES:
        # model / feature_engineering are composable building blocks; feature_engineering
        # still constrains its terminals, but neither must contain a read component.
        if pipeline_type == PipelineType.feature_engineering:
            for a in terminals:
                if comps[a] not in _WRITE | {"model-input", "comment"}:
                    report.add("INVALID_TERMINAL",
                               f"feature_engineering terminal {comps[a]!r} not allowed",
                               alias=a)
        _no_duplicate_write_names(report, by_alias)
        return

    if pipeline_type == PipelineType.scheduled:
        if not (_BATCH_READ & present):
            report.add("MISSING_READ",
                       "scheduled pipeline must contain batch-read-from-warehouse")
        for a in terminals:
            if comps[a] not in _BATCH_WRITE | {"comment"}:
                report.add("INVALID_TERMINAL",
                           f"scheduled terminal {comps[a]!r} must be "
                           "batch-write-to-warehouse", alias=a)
        _no_duplicate_write_names(report, by_alias)
        return

    # data_prep / feature-eng-as-runnable / inference / training / profiling.
    if pipeline_type != PipelineType.profiling and not (_READ & present):
        report.add("MISSING_READ",
                   f"{pipeline_type.name} pipeline must contain a read-from-warehouse")

    if pipeline_type in (PipelineType.data_prep, PipelineType.inference):
        for a in terminals:
            if comps[a] not in _WRITE | {"comment"}:
                report.add("INVALID_TERMINAL",
                           f"{pipeline_type.name} terminal {comps[a]!r} must be "
                           "write-to-warehouse", alias=a)
    _no_duplicate_write_names(report, by_alias)


def _no_duplicate_write_names(report, by_alias) -> None:
    seen: dict[str, str] = {}
    for alias, node in by_alias.items():
        if node.get("component") in _WRITE | _BATCH_WRITE:
            name = (node.get("parameters") or {}).get("output_dataset_name")
            if name and name in seen:
                report.add("DUPLICATE_OUTPUT_NAME",
                           f"output_dataset_name {name!r} also written by "
                           f"{seen[name]!r}", alias=alias)
            elif name:
                seen[name] = alias

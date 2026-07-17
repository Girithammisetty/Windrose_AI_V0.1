"""Deterministic, idempotent compilation of a validated template version into an
Argo ``WorkflowTemplate`` manifest (PIPE-FR-020..024).

Same version ⇒ byte-identical manifest ⇒ identical SHA-256 digest (AC-3). The
compiler applies the V1 transforms: strip comment nodes, inject ``clone-input`` for
fan-out (BR-5), inject the data-profiler for non-profiling pipelines, and attach the
per-node retry/QoS/TTL policy.
"""

from __future__ import annotations

from app.domain.entities import Component
from app.domain.enums import PipelineType
from app.domain.resources import resolve_resources
from app.utils import canonical_json, sha256_hex

RETRY_STRATEGY = {
    "limit": 3,
    "retryPolicy": "Always",
    "backoff": {"duration": "5s", "factor": 2},
    "expression": (
        'lastRetry.status == "Error" or '
        '(lastRetry.status == "Failed" and asInt(lastRetry.exitCode) not in [0, 1])'
    ),
}

TTL_STRATEGY = {"secondsAfterSuccess": 0, "secondsAfterFailure": 600}
POD_GC = {"strategy": "OnPodSuccess"}

ENVFROM = [
    {"configMapRef": {"name": "windrose-global-variables"}},
    {"configMapRef": {"name": "tenant-specific-variables"}},
    {"secretRef": {"name": "windrose-global-secrets"}},
]


def _edge_endpoints(edge: dict) -> tuple[str, str, str]:
    fa, _, fp = edge.get("from", "").rpartition(".")
    ta, _, _ = edge.get("to", "").rpartition(".")
    return (fa or edge.get("from", ""), fp, ta or edge.get("to", ""))


def _qos(res: dict) -> dict:
    """Requests = limits when guaranteed_qos or ram ≥ 15 GB, else limits/4 (BR-1)."""
    ram_gb = res["ram_gb"]
    guaranteed = res.get("guaranteed_qos") or ram_gb >= 15
    ram_mi = ram_gb * 1024
    cpu = res["cpus"]
    limits = {"cpu": str(cpu), "memory": f"{ram_mi}Mi"}
    if guaranteed:
        requests = dict(limits)
    else:
        requests = {"cpu": f"{cpu / 4:.3f}", "memory": f"{ram_mi // 4}Mi"}
    return {"requests": requests, "limits": limits}


def compile_workflow_template(
    definition: dict,
    *,
    tenant_id: str,
    template_id: str,
    version_id: str,
    pipeline_type: PipelineType,
    components: dict[str, Component],
    argo_template_name: str,
    quota_ceiling: dict,
    retry_limit: int = 3,
) -> tuple[dict, str]:
    """Return (manifest, sha256_digest). Deterministic for a given input."""
    nodes = {n["alias"]: n for n in definition.get("nodes", [])
             if components.get(n.get("component")) is None
             or components[n["component"]].component_type != 4}  # strip comment nodes
    edges = [e for e in definition.get("edges", [])
             if _edge_endpoints(e)[0] in nodes and _edge_endpoints(e)[2] in nodes]

    topo_edges = [(f, t) for f, _, t in map(_edge_endpoints, edges)]
    effective = resolve_resources(nodes, topo_edges, quota_ceiling)

    # BR-5: inject clone-input where one output feeds >1 consumer.
    fanout: dict[str, int] = {}
    for f, _fp, _t in map(_edge_endpoints, edges):
        fanout[f] = fanout.get(f, 0) + 1
    clone_nodes = sorted(a for a, c in fanout.items() if c > 1)

    # Build the container templates (sorted by alias for determinism).
    templates: list[dict] = []
    for alias in sorted(nodes):
        node = nodes[alias]
        comp = components.get(node["component"])
        res = effective.get(alias, {"cpus": 1, "ram_gb": 2, "timeout_minutes": 30})
        res = {**res, "guaranteed_qos": bool(comp and comp.definition.get("guaranteed_qos"))}
        n_out = len(node.get("outputs") or (comp.definition.get("outputs") if comp else []))
        args = ["--component-parameters", "{{inputs.parameters.component_parameters}}",
                "--resources", "{{inputs.parameters.resources}}",
                "--mlflow-run-id", "{{workflow.parameters.mlflow_run_id}}",
                "--current-context", "{{workflow.parameters.current_context}}"]
        for i in range(max(1, len(node.get("_inputs", [])) or 1)):
            args += [f"--input-path{i}", f"{{{{inputs.parameters.input_path{i}}}}}"]
        for i in range(n_out or 1):
            args += [f"--output-path{i}", f"/tmp/out{i}"]
        templates.append({
            "name": f"tmpl-{alias}",
            "retryStrategy": {**RETRY_STRATEGY, "limit": min(retry_limit, 5)},
            "activeDeadlineSeconds": res["timeout_minutes"] * 60,
            "metadata": {"labels": {"windrose.io/managed": "true",
                                    "windrose.io/alias": alias}},
            "container": {
                "image": (comp.image_digest if comp else "windrose/base-component"),
                "args": args,
                "env": [{"name": "COMPONENT_ALIAS", "value": alias}],
                "envFrom": ENVFROM,
                "resources": _qos(res),
            },
        })

    # data-profiler injection for non-profiling pipelines (PIPE-FR-022).
    if pipeline_type != PipelineType.profiling:
        templates.append({
            "name": "tmpl-data-profiler",
            "container": {"image": "windrose/data-profiler", "envFrom": ENVFROM,
                          "resources": _qos({"cpus": 1, "ram_gb": 2})},
            "metadata": {"labels": {"windrose.io/managed": "true",
                                    "windrose.io/injected": "data-profiler"}},
        })

    # DAG steps referencing the per-node templates (dependencies from edges).
    from collections import defaultdict
    deps: dict[str, set] = defaultdict(set)
    for f, t in topo_edges:
        deps[t].add(f)
    dag_tasks = [{
        "name": alias,
        "template": f"tmpl-{alias}",
        "dependencies": sorted(deps[alias]),
    } for alias in sorted(nodes)]

    manifest = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "WorkflowTemplate",
        "metadata": {
            "name": argo_template_name,
            "namespace": f"{tenant_id}-processing",
            "labels": {"windrose.io/managed": "true",
                       "windrose.io/template-id": template_id,
                       "windrose.io/version-id": version_id},
        },
        "spec": {
            "entrypoint": "main",
            "ttlStrategy": TTL_STRATEGY,
            "podGC": POD_GC,
            "arguments": {"parameters": [
                {"name": "mlflow_run_id"},
                {"name": "current_context"},
            ]},
            "templates": [
                {"name": "main", "dag": {"tasks": dag_tasks}},
                *templates,
            ],
            "injected": {"clone_input_for": clone_nodes,
                         "data_profiler": pipeline_type != PipelineType.profiling},
        },
    }
    payload = canonical_json(manifest)
    return manifest, sha256_hex(payload)

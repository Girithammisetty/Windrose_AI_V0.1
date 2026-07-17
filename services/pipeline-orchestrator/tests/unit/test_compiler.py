"""Argo compilation transforms + determinism (PIPE-FR-020..024, AC-3, BR-5)."""

from __future__ import annotations

from app.domain.catalog import seed_components
from app.domain.compiler import compile_workflow_template
from app.domain.enums import PipelineType
from app.domain.resources import PLATFORM_CEILING

COMPS = {c.name: c for c in seed_components()}
CEIL = dict(PLATFORM_CEILING)


def _fanout_definition():
    return {
        "nodes": [
            {"alias": "read-1", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/x"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "w1", "component": "write-to-warehouse",
             "parameters": {"output_dataset_name": "a"}, "outputs": []},
            {"alias": "w2", "component": "write-to-warehouse",
             "parameters": {"output_dataset_name": "b"}, "outputs": []}],
        "edges": [{"from": "read-1.out", "to": "w1.in1", "type": "dataframe"},
                  {"from": "read-1.out", "to": "w2.in1", "type": "dataframe"}]}


def test_compilation_is_deterministic():
    d = _fanout_definition()
    m1, dig1 = compile_workflow_template(
        d, tenant_id="t", template_id="tpl", version_id="v",
        pipeline_type=PipelineType.data_prep, components=COMPS,
        argo_template_name="wf-x", quota_ceiling=CEIL)
    m2, dig2 = compile_workflow_template(
        d, tenant_id="t", template_id="tpl", version_id="v",
        pipeline_type=PipelineType.data_prep, components=COMPS,
        argo_template_name="wf-x", quota_ceiling=CEIL)
    assert dig1 == dig2
    assert m1 == m2


def test_clone_input_injected_for_fanout():
    _, _ = compile_workflow_template(
        _fanout_definition(), tenant_id="t", template_id="tpl", version_id="v",
        pipeline_type=PipelineType.data_prep, components=COMPS,
        argo_template_name="wf-x", quota_ceiling=CEIL)
    manifest, _digest = compile_workflow_template(
        _fanout_definition(), tenant_id="t", template_id="tpl", version_id="v",
        pipeline_type=PipelineType.data_prep, components=COMPS,
        argo_template_name="wf-x", quota_ceiling=CEIL)
    assert "read-1" in manifest["spec"]["injected"]["clone_input_for"]


def test_retry_strategy_and_ttl_present():
    manifest, _ = compile_workflow_template(
        _fanout_definition(), tenant_id="t", template_id="tpl", version_id="v",
        pipeline_type=PipelineType.data_prep, components=COMPS,
        argo_template_name="wf-x", quota_ceiling=CEIL)
    assert manifest["spec"]["ttlStrategy"] == {"secondsAfterSuccess": 0,
                                               "secondsAfterFailure": 600}
    tmpl = next(t for t in manifest["spec"]["templates"] if t["name"] == "tmpl-read-1")
    assert tmpl["retryStrategy"]["limit"] == 3
    assert "asInt(lastRetry.exitCode) not in [0, 1]" in tmpl["retryStrategy"]["expression"]


def test_data_profiler_injected_for_non_profiling():
    manifest, _ = compile_workflow_template(
        _fanout_definition(), tenant_id="t", template_id="tpl", version_id="v",
        pipeline_type=PipelineType.data_prep, components=COMPS,
        argo_template_name="wf-x", quota_ceiling=CEIL)
    names = {t["name"] for t in manifest["spec"]["templates"]}
    assert "tmpl-data-profiler" in names

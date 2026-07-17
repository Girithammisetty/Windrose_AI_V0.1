"""DAG validation ACs (PIPE-FR-010..016)."""

from __future__ import annotations

from app.domain.catalog import seed_components
from app.domain.dag import validate_definition
from app.domain.enums import PipelineType
from app.domain.resources import PLATFORM_CEILING

COMPS = {c.name: c for c in seed_components()}
CEIL = dict(PLATFORM_CEILING)


def _codes(report):
    return {i["code"] for i in report.items}


def test_ac1_cycle_reports_exact_aliases():
    definition = {
        "nodes": [{"alias": "a", "component": "filter-data"},
                  {"alias": "b", "component": "filter-data"},
                  {"alias": "c", "component": "filter-data"}],
        "edges": [{"from": "a.out", "to": "b.in1", "type": "dataframe"},
                  {"from": "b.out", "to": "c.in1", "type": "dataframe"},
                  {"from": "c.out", "to": "a.in1", "type": "dataframe"}]}
    report = validate_definition(definition, pipeline_type=PipelineType.data_prep,
                                 model_type=None, components=COMPS, quota_ceiling=CEIL)
    assert not report.valid
    cycle_item = next(i for i in report.items if i["code"] == "DAG_CYCLE")
    assert set(cycle_item["cycle"]) == {"a", "b", "c"}


def test_ac2_edge_type_mismatch_names_both_types():
    # xgboost-train emits a `model`; filter-data consumes a `dataframe`.
    definition = {
        "nodes": [
            {"alias": "read-1", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/x"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "mi", "component": "model-input", "parameters": {"role": "TRAIN"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "train-1", "component": "xgboost-train",
             "outputs": [{"name": "model", "type": "model"}]},
            {"alias": "flt", "component": "filter-data",
             "parameters": {"expression": "x > 0"}}],
        "edges": [
            {"from": "read-1.out", "to": "mi.in1", "type": "dataframe"},
            {"from": "mi.out", "to": "train-1.in1", "type": "dataframe"},
            {"from": "train-1.model", "to": "flt.in1", "type": "model"}]}
    report = validate_definition(definition, pipeline_type=PipelineType.training,
                                 model_type="classification", components=COMPS,
                                 quota_ceiling=CEIL)
    mismatch = next(i for i in report.items if i["code"] == "EDGE_TYPE_MISMATCH")
    assert "model" in mismatch["problem"] and "dataframe" in mismatch["problem"]


def test_ac11_resource_inheritance_takes_elementwise_max_of_predecessors():
    definition = {
        "nodes": [
            {"alias": "p1", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/x"},
             "resources": {"cpus": 2, "ram_gb": 8, "timeout_minutes": 30},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "p2", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/y"},
             "resources": {"cpus": 4, "ram_gb": 4, "timeout_minutes": 30},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "merge-1", "component": "merge-data"}],
        "edges": [{"from": "p1.out", "to": "merge-1.in1", "type": "dataframe"},
                  {"from": "p2.out", "to": "merge-1.in2", "type": "dataframe"}]}
    report = validate_definition(definition, pipeline_type=PipelineType.data_prep,
                                 model_type=None, components=COMPS, quota_ceiling=CEIL)
    res = report.effective_resources["merge-1"]
    assert res == {"cpus": 4, "ram_gb": 8, "timeout_minutes": 30}


def test_param_enum_and_restricted_string():
    definition = {
        "nodes": [
            {"alias": "read-1", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/x"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "j", "component": "join-data",
             "parameters": {"join_type": "sideways", "on": "id"}},
            {"alias": "read-2", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/y"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "write-1", "component": "write-to-warehouse",
             "parameters": {"output_dataset_name": "bad name!!"}, "outputs": []}],
        "edges": [{"from": "read-1.out", "to": "j.in1", "type": "dataframe"},
                  {"from": "read-2.out", "to": "j.in2", "type": "dataframe"},
                  {"from": "j.out", "to": "write-1.in1", "type": "dataframe"}]}
    report = validate_definition(definition, pipeline_type=PipelineType.data_prep,
                                 model_type=None, components=COMPS, quota_ceiling=CEIL)
    codes = _codes(report)
    assert "NOT_IN_ENUM" in codes
    assert "RESTRICTED_STRING" in codes


def test_arity_violation_when_too_many_inputs():
    definition = {
        "nodes": [
            {"alias": "read-1", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/x"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "read-2", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:dataset/y"},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "flt", "component": "filter-data",
             "parameters": {"expression": "a"}}],  # max_inputs 1
        "edges": [{"from": "read-1.out", "to": "flt.in1", "type": "dataframe"},
                  {"from": "read-2.out", "to": "flt.in2", "type": "dataframe"}]}
    report = validate_definition(definition, pipeline_type=PipelineType.data_prep,
                                 model_type=None, components=COMPS, quota_ceiling=CEIL)
    assert "ARITY_VIOLATION" in _codes(report)


def test_data_prep_requires_read_and_write_terminal():
    definition = {"nodes": [{"alias": "flt", "component": "filter-data",
                             "parameters": {"expression": "a"}}], "edges": []}
    report = validate_definition(definition, pipeline_type=PipelineType.data_prep,
                                 model_type=None, components=COMPS, quota_ceiling=CEIL)
    codes = _codes(report)
    assert "MISSING_READ" in codes
    assert "INVALID_TERMINAL" in codes


def test_clean_data_prep_is_valid():
    from tests.conftest import data_prep_definition

    report = validate_definition(data_prep_definition(),
                                 pipeline_type=PipelineType.data_prep, model_type=None,
                                 components=COMPS, quota_ceiling=CEIL)
    assert report.valid, report.items

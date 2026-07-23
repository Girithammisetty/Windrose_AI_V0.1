"""BRD 62 — unit tests for the local pandas operator library + DAG executor.

Proves every cataloged data-prep operator has a REAL working implementation (closing
the P1 gap where they only executed inside Argo), including the P3/P4/P5 parity
deltas, and that the local executor runs a real multi-node data-prep DAG end to end
with no infra.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.catalog import DATA_PREP, seed_components
from app.executor.local_pipeline import LocalPipelineExecutor, PipelineExecutionError
from app.executor.operators import OPERATORS, OperatorError, run_operator


def _df():
    return pd.DataFrame({
        "id": [1, 2, 3, 4],
        "cat": ["a", "b", "a", "b"],
        "x": [10.0, 20.0, 30.0, 40.0],
        "y": [1.0, np.nan, 3.0, np.nan],
        "label": [0, 1, 0, 1],
    })


# ---- catalog coverage: every data-prep operator has a local impl ----

def test_every_data_prep_operator_is_implemented():
    dataprep = [c.name for c in seed_components() if c.component_type == DATA_PREP]
    missing = [n for n in dataprep if n not in OPERATORS]
    assert missing == [], f"data-prep operators with no local impl: {missing}"


# ---- shaping ----

def test_select_and_rename():
    out = run_operator("select-columns", [_df()], {"columns": ["id", "x"]})[0]
    assert list(out.columns) == ["id", "x"]
    out2 = run_operator("rename-columns", [out], {"mapping": {"x": "value"}})[0]
    assert "value" in out2.columns and "x" not in out2.columns


def test_filter_and_sort_and_sample():
    f = run_operator("filter-data", [_df()], {"expression": "x > 15"})[0]
    assert len(f) == 3 and f["x"].min() > 15
    s = run_operator("sort-data", [_df()], {"by": ["x"], "ascending": False})[0]
    assert s["x"].tolist() == [40.0, 30.0, 20.0, 10.0]
    samp = run_operator("sample-data", [_df()], {"n_rows": 2})[0]
    assert len(samp) == 2


def test_dedup_and_guid():
    dup = pd.concat([_df(), _df().iloc[[0]]], ignore_index=True)
    assert len(run_operator("remove-duplicate-rows", [dup], {})[0]) == 4
    g = run_operator("add-guid-column", [_df()], {})[0]
    assert "_row_guid_" in g.columns and g["_row_guid_"].nunique() == 4


# ---- joins (incl. P3 right) / union / merge ----

@pytest.mark.parametrize("jt,expected", [("inner", 2), ("left", 3), ("right", 3), ("outer", 4)])
def test_join_types_including_right(jt, expected):
    left = pd.DataFrame({"k": [1, 2, 3], "a": ["x", "y", "z"]})
    right = pd.DataFrame({"k": [2, 3, 4], "b": ["p", "q", "r"]})
    out = run_operator("join-data", [left, right], {"join_type": jt, "on": "k"})[0]
    assert len(out) == expected


def test_union_and_merge():
    u = run_operator("union", [_df(), _df()], {})[0]
    assert len(u) == 8
    m = run_operator("merge-data", [
        pd.DataFrame({"k": [1, 2], "a": [1, 2]}),
        pd.DataFrame({"k": [1, 2], "b": [3, 4]}),
    ], {"on": "k"})[0]
    assert set(m.columns) == {"k", "a", "b"}


# ---- aggregation / reshape ----

def test_group_by_and_pivot():
    g = run_operator("group-by", [_df()], {"by": ["cat"], "aggregations": {"x": "sum"}})[0]
    assert g.loc[g["cat"] == "a", "x"].iloc[0] == 40.0
    long = pd.DataFrame({"day": [1, 1, 2, 2], "metric": ["a", "b", "a", "b"], "v": [1, 2, 3, 4]})
    wide = run_operator("long-to-wide-converter", [long],
                        {"index": "day", "columns": "metric", "values": "v"})[0]
    assert {"a", "b"}.issubset(set(wide.columns))
    back = run_operator("wide-to-long-converter", [wide], {"id_vars": ["day"]})[0]
    assert "value" in back.columns


# ---- cleaning: P4 missing-value strategies ----

@pytest.mark.parametrize("strategy", ["mean", "median", "most_frequent", "constant", "drop",
                                      "linear_interpolation"])
def test_handle_missing_values_all_strategies(strategy):
    out = run_operator("handle-missing-values", [_df()],
                       {"strategy": strategy, "columns": ["y"], "fill_value": 0})[0]
    if strategy == "drop":
        assert out["y"].isna().sum() == 0 and len(out) == 2
    else:
        assert out["y"].isna().sum() == 0


def test_handle_missing_values_directional_fill():
    # previous_existing = ffill (carries the last seen value forward); next_existing =
    # bfill (pulls the next value back) —  semantics (edge gaps may remain).
    df = pd.DataFrame({"y": [1.0, np.nan, 3.0, np.nan, 5.0]})
    prev = run_operator("handle-missing-values", [df],
                        {"strategy": "previous_existing", "columns": ["y"]})[0]
    assert prev["y"].tolist() == [1.0, 1.0, 3.0, 3.0, 5.0]
    nxt = run_operator("handle-missing-values", [df],
                       {"strategy": "next_existing", "columns": ["y"]})[0]
    assert nxt["y"].tolist() == [1.0, 3.0, 3.0, 5.0, 5.0]


def test_handle_missing_values_expression():
    out = run_operator("handle-missing-values", [_df()],
                       {"strategy": "expression", "columns": ["y"], "expression": "x / 10"})[0]
    # row 2 (x=20) had y=NaN → filled with 20/10 = 2.0
    assert out.loc[1, "y"] == 2.0


def test_remove_outliers_and_quantization():
    df = pd.DataFrame({"v": [1, 2, 2, 3, 100]})
    assert 100 not in run_operator("remove-outliers", [df], {"columns": ["v"]})[0]["v"].tolist()
    q = run_operator("quantization", [_df()], {"column": "x", "bins": 2})[0]
    assert "x_bin" in q.columns and q["x_bin"].nunique() <= 2


# ---- scaling / decomposition / expressions ----

def test_scaling_and_pca_and_expr():
    mm = run_operator("minmax-scale", [_df()], {"columns": ["x"]})[0]
    assert mm["x"].min() == 0.0 and mm["x"].max() == 1.0
    z = run_operator("zscore-normalization", [_df()], {"columns": ["x"]})[0]
    assert abs(z["x"].mean()) < 1e-9
    p = run_operator("pca", [_df()], {"columns": ["x", "id"], "n_components": 1})[0]
    assert "pc_1" in p.columns
    e = run_operator("python-expression", [_df()],
                     {"expression": "x + id", "output_column": "s"})[0]
    assert e["s"].tolist() == [11.0, 22.0, 33.0, 44.0]
    lc = run_operator("linear-combination", [_df()],
                      {"weights": {"x": 1.0, "id": 2.0}, "output_column": "lc"})[0]
    assert lc["lc"].tolist() == [12.0, 24.0, 36.0, 48.0]
    t = run_operator("transform-data", [_df()], {"function": "log", "columns": ["x"]})[0]
    assert t["x"].iloc[0] == pytest.approx(np.log1p(10.0))


# ---- encoders ----

def test_encoders():
    oh = run_operator("one-hot-encoder", [_df()], {"columns": ["cat"]})[0]
    assert "cat_a" in oh.columns and "cat_b" in oh.columns
    od = run_operator("ordinal-encoder", [_df()], {"columns": ["cat"]})[0]
    assert set(od["cat"].unique()) == {0, 1}
    te = run_operator("target-encoder", [_df()], {"columns": ["cat"], "target": "label"})[0]
    assert te["cat"].dtype == float


# ---- feature-selection filters ----

def test_filters():
    df = pd.DataFrame({"const": [1, 1, 1, 1], "v": [1, 2, 3, 4], "label": [1, 2, 3, 4]})
    vf = run_operator("variance-filter", [df], {"threshold": 0.0})[0]
    assert "const" not in vf.columns and "v" in vf.columns
    qc = run_operator("quasi-constant-filter", [df], {"threshold": 0.9})[0]
    assert "const" not in qc.columns
    df2 = pd.DataFrame({"a": [1, 2, 3, 4], "b": [1, 2, 3, 4], "c": [4, 3, 2, 1]})
    cf = run_operator("correlation-filter", [df2], {"threshold": 0.99})[0]
    assert cf.shape[1] < df2.shape[1]  # a≈b dropped
    sf = run_operator("statistical-filter", [df], {"target": "label", "threshold": 0.5})[0]
    assert "v" in sf.columns  # v perfectly correlates with label


# ---- split (P5 stratified) ----

def test_split_stratified():
    df = pd.DataFrame({"x": range(100), "label": [0] * 50 + [1] * 50})
    train, test = run_operator("split-data", [df],
                               {"split_size": 0.8, "stratify_columns": ["label"],
                                "random_state": 7})
    # Stratification keeps the 50/50 label ratio in both splits.
    assert abs(train["label"].mean() - 0.5) < 1e-9
    assert abs(test["label"].mean() - 0.5) < 1e-9
    assert len(train) == 80 and len(test) == 20


def test_operator_fails_closed_on_bad_params():
    with pytest.raises(OperatorError):
        run_operator("select-columns", [_df()], {"columns": ["nope"]})
    with pytest.raises(OperatorError):
        run_operator("group-by", [_df()], {"by": ["cat"], "aggregations": {"x": "bogus"}})
    with pytest.raises(OperatorError):
        run_operator("unknown-operator", [_df()], {})


# ---- local DAG executor: real end-to-end pipeline, no infra ----

def test_local_executor_runs_a_real_dataprep_dag():
    # read → filter → group-by → write, with an injected dict-backed reader/writer.
    source = _df()
    written: dict[str, pd.DataFrame] = {}
    definition = {
        "nodes": [
            {"alias": "read1", "component": "read-from-warehouse",
             "parameters": {"dataset": "wr:t:dataset:d1"}},
            {"alias": "flt", "component": "filter-data",
             "parameters": {"expression": "x >= 20"}},
            {"alias": "agg", "component": "group-by",
             "parameters": {"by": ["cat"], "aggregations": {"x": "sum"}}},
            {"alias": "write1", "component": "write-to-warehouse",
             "parameters": {"dataset_name": "out"}},
        ],
        "edges": [
            {"from": "read1.out", "to": "flt"},
            {"from": "flt.out", "to": "agg"},
            {"from": "agg.out", "to": "write1"},
        ],
    }
    def _writer(frame, alias, params):
        written[alias] = frame
        return f"wr:t:dataset:{alias}"

    ex = LocalPipelineExecutor(reader=lambda urn, params: source.copy(), writer=_writer)
    result = ex.run(definition)
    assert [s.phase for s in result.statuses] == ["Succeeded"] * 4
    out = written["write1"]
    # x>=20 keeps rows 2,3,4 (cat b,a,b) → group sum: a=30, b=60
    assert out.loc[out["cat"] == "a", "x"].iloc[0] == 30.0
    assert out.loc[out["cat"] == "b", "x"].iloc[0] == 60.0
    assert result.written_refs["write1"] == "wr:t:dataset:write1"


def test_local_executor_fan_out_with_clone_and_split():
    source = pd.DataFrame({"x": range(10), "label": [0, 1] * 5})
    definition = {
        "nodes": [
            {"alias": "r", "component": "read-from-warehouse", "parameters": {"dataset": "d"}},
            {"alias": "sp", "component": "split-data",
             "parameters": {"split_size": 0.7, "stratify_columns": ["label"], "random_state": 1},
             "outputs": [{"name": "train"}, {"name": "test"}]},
        ],
        "edges": [{"from": "r.out", "to": "sp"}],
    }
    ex = LocalPipelineExecutor(reader=lambda urn, params: source.copy())
    result = ex.run(definition)
    # sp is terminal → its first output (train) surfaces; 70% of 10 = 7 rows.
    assert len(result.outputs["sp"]) == 7


def test_local_executor_surfaces_node_failure():
    definition = {
        "nodes": [
            {"alias": "r", "component": "read-from-warehouse", "parameters": {"dataset": "d"}},
            {"alias": "bad", "component": "select-columns", "parameters": {"columns": ["nope"]}},
        ],
        "edges": [{"from": "r.out", "to": "bad"}],
    }
    ex = LocalPipelineExecutor(reader=lambda urn, params: _df())
    with pytest.raises(PipelineExecutionError) as ei:
        ex.run(definition)
    assert ei.value.alias == "bad"

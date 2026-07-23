"""BRD 65 — warehouse write-back sinks: the real local/object-store sinks persist a
computed DataFrame durably; the cloud sinks (athena/bigquery/synapse) fail CLOSED
with DependencyUnavailable when unconfigured (honest, never a faked write).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.config import Settings
from app.domain.errors import DependencyUnavailable
from app.executor.sinks import WAREHOUSE_SINKS, LocalFsSink


def _df():
    return pd.DataFrame({"cat": ["a", "b"], "x": [1.0, 2.0]})


def test_registry_has_all_backends():
    assert set(WAREHOUSE_SINKS.names()) == {"local", "objectstore", "athena",
                                            "bigquery", "synapse"}
    with pytest.raises(ValueError):
        WAREHOUSE_SINKS.create("nope", Settings())


def test_local_sink_writes_real_parquet(tmp_path):
    sink = LocalFsSink(str(tmp_path))
    res = sink.write_frame(_df(), tenant_id="t1", name="My Output")
    assert res.rows == 2 and res.backend == "local"
    assert res.ref == "wr:t1:dataset:warehouse/my_output"
    # The parquet is real + round-trips.
    path = res.uri.removeprefix("file://")
    back = pd.read_parquet(path)
    assert list(back.columns) == ["cat", "x"] and len(back) == 2


def test_local_sink_via_registry(tmp_path):
    s = WAREHOUSE_SINKS.create("local", Settings(object_store_dir=str(tmp_path)))
    res = s.write_frame(_df(), tenant_id="t1", name="out")
    assert res.rows == 2


@pytest.mark.parametrize("backend", ["athena", "bigquery", "synapse"])
def test_cloud_sinks_fail_closed_when_unconfigured(backend):
    sink = WAREHOUSE_SINKS.create(backend, Settings())  # no warehouse_conn
    with pytest.raises(DependencyUnavailable):
        sink.write_frame(_df(), tenant_id="t1", name="out")


def test_cloud_sink_reaches_write_path_when_configured():
    # With connection config present, the unconfigured-guard passes and the sink
    # reaches its real (cloud-only) write path — which still DependencyUnavailable on
    # the Mac (no cloud), proving it's gated on infra, not faking success.
    s = Settings(warehouse_conn={"bigquery": {"dataset": "proj.ds"}})
    sink = WAREHOUSE_SINKS.create("bigquery", s)
    with pytest.raises(DependencyUnavailable) as ei:
        sink.write_frame(_df(), tenant_id="t1", name="out")
    assert "proj.ds" in str(ei.value)  # got past the config guard into the write path

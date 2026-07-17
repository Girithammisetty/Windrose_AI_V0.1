"""AC-11: the metric filter+sort is index-served — EXPLAIN shows the
run_metrics (tenant_id, key, value DESC) index (ix_run_metrics_kv) in use, not a
sequential scan."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import TENANT_A, ctx_for, make_experiment, seed_finished_run

pytestmark = pytest.mark.integration


async def test_metric_query_uses_index(container, engine):
    ctx = ctx_for(TENANT_A)
    exp = await make_experiment(container, ctx, name=f"idx-{uuid.uuid4().hex[:8]}")
    for i in range(5):
        await seed_finished_run(container, ctx, exp.id, mlflow_run_id=f"idx-{i}",
                                metrics={"f1_score": 0.5 + i / 10})

    async with engine.connect() as conn:
        # discourage seq-scan so the planner reveals the index it *would* use on
        # a large table (the AC-11 index), independent of tiny test-row counts.
        await conn.execute(text("SET enable_seqscan = off"))
        await conn.execute(text("SET enable_bitmapscan = off"))
        rows = (await conn.execute(text(
            "EXPLAIN SELECT run_id FROM run_metrics "
            "WHERE tenant_id = :t AND key = 'f1_score' AND value >= 0.9 "
            "ORDER BY value DESC"
        ), {"t": TENANT_A})).scalars().all()
    plan = "\n".join(rows)
    assert "ix_run_metrics_kv" in plan, plan
    assert "Seq Scan" not in plan, plan

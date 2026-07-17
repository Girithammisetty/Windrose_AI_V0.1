"""EXP-FR-020, BR-9/BR-10, AC-5: server-side run comparison."""

from __future__ import annotations

import pytest

from app.domain.errors import NotFound, ValidationFailed
from tests.conftest import ctx_for, make_experiment, seed_finished_run


async def _two_runs(container, ctx):
    exp = await make_experiment(container, ctx)
    a = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="A",
                                metrics={"f1_score": 0.91, "rmse": 0.30},
                                params={"max_depth": "6"})
    b = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="B",
                                metrics={"f1_score": 0.87, "rmse": 0.20},
                                params={"max_depth": "8"})
    return a, b


async def test_compare_best_run_direction_ac5(container):
    ctx = ctx_for()
    a, b = await _two_runs(container, ctx)
    result = await container.compare_service.compare(
        ctx, run_ids=[a.id, b.id], metrics=["f1_score", "rmse"], params=None,
        include_all=False, cursor=None)
    metrics = {m["key"]: m for m in result["metrics"]}
    assert metrics["f1_score"]["best_run_id"] == a.id  # max
    assert metrics["f1_score"]["direction"] == "max"
    assert metrics["rmse"]["best_run_id"] == b.id  # loss -> min
    assert metrics["rmse"]["direction"] == "min"


async def test_compare_param_differs(container):
    ctx = ctx_for()
    a, b = await _two_runs(container, ctx)
    result = await container.compare_service.compare(
        ctx, run_ids=[a.id, b.id], metrics=None, params=["max_depth"],
        include_all=False, cursor=None)
    params = {p["key"]: p for p in result["params"]}
    assert params["max_depth"]["differs"] is True


async def test_compare_rejects_duplicate_and_count(container):
    ctx = ctx_for()
    a, _ = await _two_runs(container, ctx)
    with pytest.raises(ValidationFailed):
        await container.compare_service.compare(
            ctx, run_ids=[a.id, a.id], metrics=None, params=None,
            include_all=True, cursor=None)
    with pytest.raises(ValidationFailed):
        await container.compare_service.compare(
            ctx, run_ids=[a.id], metrics=None, params=None, include_all=True, cursor=None)


async def test_compare_non_visible_run_is_404(container):
    ctx = ctx_for()
    a, _ = await _two_runs(container, ctx)
    with pytest.raises(NotFound):
        await container.compare_service.compare(
            ctx, run_ids=[a.id, "does-not-exist"], metrics=None, params=None,
            include_all=True, cursor=None)

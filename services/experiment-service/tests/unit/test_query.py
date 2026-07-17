"""EXP-FR-050/051, BR-10: indexed metric/param query + best run."""

from __future__ import annotations

import pytest

from app.domain.errors import ValidationFailed
from tests.conftest import ctx_for, make_experiment, seed_finished_run


async def _seed(container, ctx):
    exp = await make_experiment(container, ctx, name="q-exp")
    await seed_finished_run(container, ctx, exp.id, mlflow_run_id="q1",
                            metrics={"f1_score": 0.95}, params={"max_depth": "6"})
    await seed_finished_run(container, ctx, exp.id, mlflow_run_id="q2",
                            metrics={"f1_score": 0.80}, params={"max_depth": "8"})
    await seed_finished_run(container, ctx, exp.id, mlflow_run_id="q3",
                            metrics={"f1_score": 0.90}, params={"max_depth": "6"})
    return exp


async def test_metric_predicate_and_sort(container):
    ctx = ctx_for()
    exp = await _seed(container, ctx)
    page = await container.query_service.search_runs(
        ctx, experiment_ids=[exp.id], status="finished", algorithm=None, tag=None,
        metric_predicates=[("f1_score", "gte", 0.9)], param_predicates=[],
        sort="-metric.f1_score", limit=50, cursor=None)
    ids = [r.mlflow_run_id for r in page.items]
    assert ids == ["q1", "q3"]  # >=0.9, sorted desc


async def test_param_predicate(container):
    ctx = ctx_for()
    exp = await _seed(container, ctx)
    page = await container.query_service.search_runs(
        ctx, experiment_ids=[exp.id], status=None, algorithm=None, tag=None,
        metric_predicates=[], param_predicates=[("max_depth", "6")],
        sort="-created_at", limit=50, cursor=None)
    assert {r.mlflow_run_id for r in page.items} == {"q1", "q3"}


async def test_too_many_predicates_422(container):
    ctx = ctx_for()
    exp = await _seed(container, ctx)
    with pytest.raises(ValidationFailed):
        await container.query_service.search_runs(
            ctx, experiment_ids=[exp.id], status=None, algorithm=None, tag=None,
            metric_predicates=[("a", "gte", 1), ("b", "gte", 1), ("c", "gte", 1),
                               ("d", "gte", 1)],
            param_predicates=[], sort="-created_at", limit=50, cursor=None)


async def test_best_run(container):
    ctx = ctx_for()
    exp = await _seed(container, ctx)
    best = await container.query_service.best_run(ctx, exp.id, "f1_score", "max", "finished")
    assert best["mlflow_run_id"] == "q1"

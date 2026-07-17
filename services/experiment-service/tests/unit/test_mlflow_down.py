"""BR-8 / AC-13: MLflow outage — reads served from the mirror (200); experiment
creation degrades to 503 (DEPENDENCY_UNAVAILABLE)."""

from __future__ import annotations

import pytest

from app.domain.errors import DependencyUnavailable
from tests.conftest import ctx_for, make_experiment, seed_finished_run


class _DownMlflow:
    async def create_experiment(self, name, tags=None):
        raise DependencyUnavailable("MLflow is down")

    async def set_experiment_tag(self, *a, **k):
        raise DependencyUnavailable("MLflow is down")

    async def delete_run(self, *a, **k):
        raise DependencyUnavailable("MLflow is down")


async def test_reads_ok_and_create_503_when_mlflow_down(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="pre-outage")
    run = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="r-down",
                                  metrics={"f1_score": 0.9})

    # Simulate the MLflow outage.
    container.deps.mlflow = _DownMlflow()

    # Reads are served entirely from the Postgres/in-memory mirror (AC-13).
    detail = await container.run_service.get_detail(ctx, run.id)
    assert detail["metrics"]["f1_score"]["value"] == 0.9
    best = await container.query_service.best_run(ctx, exp.id, "f1_score", "max", "finished")
    assert best["mlflow_run_id"] == "r-down"

    # Experiment creation requires the mandatory MLflow experiment id -> 503.
    with pytest.raises(DependencyUnavailable):
        await make_experiment(container, ctx, name="during-outage")

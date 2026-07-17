"""Experiments + their runs (BRD §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import (
    ExperimentCreate,
    ExperimentPatch,
    page_envelope,
)
from app.domain.entities import MODEL_TYPE_LABELS

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


def _experiment_payload(exp) -> dict:
    return {
        "id": exp.id, "name": exp.name, "workspace_id": exp.workspace_id,
        "model_type": MODEL_TYPE_LABELS[exp.model_type],
        "mlflow_experiment_id": exp.mlflow_experiment_id,
        "model_pipeline_urn": exp.model_pipeline_urn,
        "feature_engineering_pipeline_urn": exp.feature_engineering_pipeline_urn,
        "training_pipeline_urn": exp.training_pipeline_urn,
        "description": exp.description, "note": exp.note, "tags": exp.tags,
        "archived": exp.deleted_at is not None,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
    }


@router.post("/experiments", status_code=201)
async def create_experiment(
    request: Request, response: Response, body: ExperimentCreate,
    principal: Principal = Depends(require("experiment.experiment.create")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id, workspace_id=body.workspace_id)

    async def work():
        exp = await c.experiment_service.create(ctx, body.model_dump())
        return 201, {"data": _experiment_payload(exp)}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/experiments")
async def list_experiments(
    request: Request,
    principal: Principal = Depends(require("experiment.experiment.read")),
    workspace_id: str | None = Query(default=None, alias="filter[workspace_id]"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.experiment_service.list(
        principal.ctx(request.state.trace_id), workspace_id, limit, cursor)
    return page_envelope([_experiment_payload(e) for e in page.items],
                         page.next_cursor, page.has_more)


@router.get("/experiments/list_archived")
async def list_archived(
    request: Request,
    principal: Principal = Depends(require("experiment.experiment.read")),
    workspace_id: str | None = Query(default=None, alias="filter[workspace_id]"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.experiment_service.list(
        principal.ctx(request.state.trace_id), workspace_id, limit, cursor, archived=True)
    return page_envelope([_experiment_payload(e) for e in page.items],
                         page.next_cursor, page.has_more)


@router.get("/experiments/{experiment_id}")
async def get_experiment(
    request: Request, experiment_id: str,
    principal: Principal = Depends(require("experiment.experiment.read")),
):
    c = _c(request)
    exp = await c.experiment_service.get(principal.ctx(request.state.trace_id), experiment_id)
    return {"data": _experiment_payload(exp)}


@router.patch("/experiments/{experiment_id}")
async def patch_experiment(
    request: Request, experiment_id: str, body: ExperimentPatch,
    principal: Principal = Depends(require("experiment.experiment.update")),
):
    c = _c(request)
    exp = await c.experiment_service.patch(
        principal.ctx(request.state.trace_id), experiment_id,
        body.model_dump(exclude_unset=True))
    return {"data": _experiment_payload(exp)}


@router.delete("/experiments/{experiment_id}")
async def archive_experiment(
    request: Request, experiment_id: str,
    principal: Principal = Depends(require("experiment.experiment.delete")),
):
    c = _c(request)
    exp = await c.experiment_service.archive(principal.ctx(request.state.trace_id), experiment_id)
    return {"data": _experiment_payload(exp)}


@router.patch("/experiments/{experiment_id}/restore")
async def restore_experiment(
    request: Request, experiment_id: str,
    principal: Principal = Depends(require("experiment.experiment.update")),
):
    c = _c(request)
    exp = await c.experiment_service.restore(principal.ctx(request.state.trace_id), experiment_id)
    return {"data": _experiment_payload(exp)}


@router.get("/experiments/{experiment_id}/runs")
async def list_experiment_runs(
    request: Request, experiment_id: str,
    principal: Principal = Depends(require("experiment.run.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    from app.domain.services import _run_payload

    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)
    page = await c.run_service.list(ctx, experiment_id, limit, cursor)
    return page_envelope([_run_payload(ctx, r) for r in page.items],
                         page.next_cursor, page.has_more)


@router.get("/experiments/{experiment_id}/runs/best")
async def best_run(
    request: Request, experiment_id: str,
    principal: Principal = Depends(require("experiment.run.read")),
    metric: str = Query(...),
    direction: str = "max",
    status: str | None = None,
):
    c = _c(request)
    payload = await c.query_service.best_run(
        principal.ctx(request.state.trace_id), experiment_id, metric, direction, status)
    return {"data": payload}


@router.post("/experiments/{experiment_id}/runs/{run_id}/register", status_code=201)
async def register_run(
    request: Request, response: Response, experiment_id: str, run_id: str,
    principal: Principal = Depends(require("experiment.model.create")),
):
    from app.api.schemas import RegisterRequest

    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)
    body = RegisterRequest(**(await request.json()))

    async def work():
        result = await c.registry_service.register(ctx, experiment_id, run_id, body.model_dump())
        return 201, {"data": result}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)

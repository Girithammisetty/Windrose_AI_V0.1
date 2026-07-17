from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import Principal, require
from app.api.schemas import RunCreate, data
from app.api.serialize import dump, dump_page

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.post("/runs", status_code=201)
async def create_run(
    request: Request, body: RunCreate, principal: Principal = Depends(require("eval.run.execute"))
):
    container = request.app.state.container
    provider = container.candidate_provider(body.candidate_outputs)
    run = await container.run_service.create_and_execute(
        _ctx(request, principal),
        trigger=body.trigger,
        agent_key=body.agent_key,
        candidate=body.candidate,
        suite_id=body.suite_id,
        suite_version=body.suite_version,
        candidate_provider=provider,
        baseline=body.baseline,
        memory_snapshot_ver=body.memory_snapshot_ver,
        cost_cap_usd=body.cost_cap_usd,
    )
    return data(dump(run))


@router.get("/runs")
async def list_runs(
    request: Request,
    agent_key: str | None = None,
    trigger: str | None = None,
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    principal: Principal = Depends(require("eval.run.read")),
):
    svc = request.app.state.container.run_service
    return dump_page(await svc.list(_ctx(request, principal), agent_key, trigger, limit, cursor))


@router.get("/runs/{run_id}")
async def get_run(
    request: Request, run_id: str, principal: Principal = Depends(require("eval.run.read"))
):
    svc = request.app.state.container.run_service
    return data(dump(await svc.get(_ctx(request, principal), run_id)))


@router.get("/runs/{run_id}/cases")
async def get_run_cases(
    request: Request, run_id: str, principal: Principal = Depends(require("eval.run.read"))
):
    svc = request.app.state.container.run_service
    results = await svc.list_cases(_ctx(request, principal), run_id)
    return data([dump(r) for r in results])


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    request: Request, run_id: str, principal: Principal = Depends(require("eval.run.execute"))
):
    svc = request.app.state.container.run_service
    return data(dump(await svc.cancel(_ctx(request, principal), run_id)))

"""Runs: detail, update, note, search, compare, metric-history, artifacts."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.schemas import CompareRequest, NoteBody, RunPatch, page_envelope

router = APIRouter(prefix="/api/v1")

_METRIC_RE = re.compile(r"^metric\[(?P<key>[^\]]+)\]\[(?P<op>gte|lte|gt|lt|eq)\]$")
_PARAM_RE = re.compile(r"^param\[(?P<key>[^\]]+)\]$")


def _c(request: Request):
    return request.app.state.container


@router.get("/runs")
async def search_runs(
    request: Request,
    principal: Principal = Depends(require("experiment.run.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    sort: str = "-created_at",
    experiment_id: str | None = Query(default=None, alias="filter[experiment_id]"),
    status: str | None = Query(default=None, alias="filter[status]"),
    algorithm: str | None = Query(default=None, alias="filter[algorithm]"),
    tag: str | None = Query(default=None, alias="filter[tag]"),
):
    from app.domain.services import _run_payload

    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)
    # filter[experiment_id] accepts a comma-separated id set (IN filter) so the
    # bff dataloader can batch runs across experiments without N+1. Single id
    # stays a one-element IN. Tenant scoping is enforced by RLS.
    experiment_ids = (
        [x for x in (s.strip() for s in experiment_id.split(",")) if x]
        if experiment_id else None)
    metric_predicates: list[tuple[str, str, float]] = []
    param_predicates: list[tuple[str, str]] = []
    for raw_key, value in request.query_params.multi_items():
        m = _METRIC_RE.match(raw_key)
        if m:
            metric_predicates.append((m.group("key"), m.group("op"), float(value)))
            continue
        p = _PARAM_RE.match(raw_key)
        if p:
            param_predicates.append((p.group("key"), value))
    page = await c.query_service.search_runs(
        ctx, experiment_ids=experiment_ids, status=status, algorithm=algorithm, tag=tag,
        metric_predicates=metric_predicates, param_predicates=param_predicates,
        sort=sort, limit=limit, cursor=cursor)
    return page_envelope([_run_payload(ctx, r) for r in page.items],
                         page.next_cursor, page.has_more)


@router.post("/runs/compare")
async def compare_runs(
    request: Request, body: CompareRequest,
    principal: Principal = Depends(require("experiment.run.read")),
    cursor: str | None = None,
):
    c = _c(request)
    result = await c.compare_service.compare(
        principal.ctx(request.state.trace_id), run_ids=body.run_ids, metrics=body.metrics,
        params=body.params, include_all=body.include_all, cursor=cursor)
    return {"data": {"runs": result["runs"], "metrics": result["metrics"],
                     "params": result["params"]},
            "page": {"next_cursor": result["next_cursor"], "has_more": result["has_more"]}}


@router.get("/runs/{run_id}")
async def get_run(
    request: Request, run_id: str,
    principal: Principal = Depends(require("experiment.run.read")),
    include_hidden: bool = False,
):
    c = _c(request)
    detail = await c.run_service.get_detail(
        principal.ctx(request.state.trace_id), run_id, include_hidden)
    return {"data": detail}


@router.patch("/runs/{run_id}")
async def patch_run(
    request: Request, run_id: str, body: RunPatch,
    principal: Principal = Depends(require("experiment.run.update")),
):
    c = _c(request)
    detail = await c.run_service.update(
        principal.ctx(request.state.trace_id), run_id, body.model_dump(exclude_unset=True))
    return {"data": detail}


@router.delete("/runs/{run_id}")
async def delete_run(
    request: Request, run_id: str,
    principal: Principal = Depends(require("experiment.run.delete")),
):
    c = _c(request)
    await c.run_service.delete(principal.ctx(request.state.trace_id), run_id)
    return {"data": {"id": run_id, "deleted": True}}


@router.post("/runs/{run_id}/note", status_code=201)
@router.put("/runs/{run_id}/note")
async def upsert_note(
    request: Request, run_id: str, body: NoteBody,
    principal: Principal = Depends(require("experiment.run.update")),
):
    c = _c(request)
    desc = await c.run_service.set_note(
        principal.ctx(request.state.trace_id), run_id, body.description)
    return {"data": {"run_id": run_id, "description": desc}}


@router.patch("/runs/{run_id}/note")
async def patch_note(
    request: Request, run_id: str, body: NoteBody,
    principal: Principal = Depends(require("experiment.run.update")),
):
    c = _c(request)
    desc = await c.run_service.set_note(
        principal.ctx(request.state.trace_id), run_id, body.description)
    return {"data": {"run_id": run_id, "description": desc}}


@router.get("/runs/{run_id}/note")
async def get_note(
    request: Request, run_id: str,
    principal: Principal = Depends(require("experiment.run.read")),
):
    c = _c(request)
    desc = await c.run_service.get_note(principal.ctx(request.state.trace_id), run_id)
    return {"data": {"run_id": run_id, "description": desc}}


@router.delete("/runs/{run_id}/note")
async def delete_note(
    request: Request, run_id: str,
    principal: Principal = Depends(require("experiment.run.update")),
):
    c = _c(request)
    await c.run_service.delete_note(principal.ctx(request.state.trace_id), run_id)
    return {"data": {"run_id": run_id, "note_deleted": True}}


@router.get("/runs/{run_id}/metric-history")
async def metric_history(
    request: Request, run_id: str,
    principal: Principal = Depends(require("experiment.run.read")),
    keys: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = None,
):
    c = _c(request)
    key_list = keys.split(",") if keys else None
    page = await c.compare_service.metric_history(
        principal.ctx(request.state.trace_id), run_id, key_list, limit, cursor)
    return page_envelope(page.items, page.next_cursor, page.has_more)


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    request: Request, run_id: str,
    principal: Principal = Depends(require("experiment.run.read")),
):
    c = _c(request)
    arts = await c.run_service.artifacts(principal.ctx(request.state.trace_id), run_id)
    return {"data": arts}


@router.get("/runs/{run_id}/artifacts/url")
async def artifact_url(
    request: Request, run_id: str, path: str,
    principal: Principal = Depends(require("experiment.run.read")),
    response: Response = None,
):
    c = _c(request)
    url = await c.run_service.artifact_url(principal.ctx(request.state.trace_id), run_id, path)
    return {"data": {"url": url, "path": path}}

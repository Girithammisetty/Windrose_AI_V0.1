"""Verified-query lifecycle + semantic search (SEM-FR-040..043)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import (
    CandidateCreate,
    DecisionBody,
    VerifiedQueryCreate,
    VerifiedQueryPatch,
    page_envelope,
)
from app.domain.services import vq_payload

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.post("/verified-queries", status_code=201)
async def create_verified_query(
    request: Request,
    response: Response,
    body: VerifiedQueryCreate,
    principal: Principal = Depends(require("semantic.verified_query.create")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        vq = await c.verified_query_service.create(ctx, body.model_dump())
        return 201, {"data": vq_payload(vq)}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.post("/verified-queries/candidates", status_code=201)
async def create_candidate(
    request: Request,
    response: Response,
    body: CandidateCreate,
    principal: Principal = Depends(require("semantic.verified_query.create")),
):
    """SEM-FR-042: harvested drafts with agent-run provenance."""
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        vq = await c.verified_query_service.create(
            ctx, body.model_dump(exclude={"agent_run_urn"}),
            provenance={"agent_run_urn": body.agent_run_urn, "origin": "harvested"})
        return 201, {"data": vq_payload(vq)}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/verified-queries")
async def list_verified_queries(
    request: Request,
    principal: Principal = Depends(require("semantic.verified_query.read")),
    workspace_id: str | None = Query(default=None, alias="filter[workspace_id]"),
    status: str | None = Query(default=None, alias="filter[status]"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.verified_query_service.list(
        principal.ctx(request.state.trace_id), workspace_id, status, limit, cursor)
    return page_envelope([vq_payload(vq) for vq in page.items],
                         page.next_cursor, page.has_more)


# NB: registered before /verified-queries/{vq_id} so "search" never matches an id.
@router.get("/verified-queries:search")
@router.get("/verified-queries/search")
async def search_verified_queries(
    request: Request,
    q: str,
    workspace_id: str,
    top_k: int = Query(default=5, ge=1, le=10),
    principal: Principal = Depends(require("semantic.verified_query.read")),
):
    c = _c(request)
    results = await c.verified_query_service.search(
        principal.ctx(request.state.trace_id), workspace_id, q, top_k)
    return {"data": results}


@router.get("/verified-queries/{vq_id}")
async def get_verified_query(
    request: Request,
    vq_id: str,
    principal: Principal = Depends(require("semantic.verified_query.read")),
):
    c = _c(request)
    vq = await c.verified_query_service.get(
        principal.ctx(request.state.trace_id), vq_id)
    return {"data": vq_payload(vq)}


@router.patch("/verified-queries/{vq_id}")
async def patch_verified_query(
    request: Request,
    vq_id: str,
    body: VerifiedQueryPatch,
    principal: Principal = Depends(require("semantic.verified_query.update")),
):
    c = _c(request)
    vq = await c.verified_query_service.patch(
        principal.ctx(request.state.trace_id), vq_id,
        body.model_dump(exclude_unset=True))
    return {"data": vq_payload(vq)}


@router.post("/verified-queries/{vq_id}/submit")
async def submit_verified_query(
    request: Request,
    vq_id: str,
    principal: Principal = Depends(require("semantic.verified_query.update")),
):
    c = _c(request)
    vq = await c.verified_query_service.submit(
        principal.ctx(request.state.trace_id), vq_id)
    return {"data": vq_payload(vq)}


@router.post("/verified-queries/{vq_id}/approve")
async def approve_verified_query(
    request: Request,
    vq_id: str,
    principal: Principal = Depends(require("semantic.verified_query.approve")),
):
    c = _c(request)
    vq = await c.verified_query_service.approve(
        principal.ctx(request.state.trace_id), vq_id)
    return {"data": vq_payload(vq)}


@router.post("/verified-queries/{vq_id}/reject")
async def reject_verified_query(
    request: Request,
    vq_id: str,
    body: DecisionBody,
    principal: Principal = Depends(require("semantic.verified_query.approve")),
):
    c = _c(request)
    vq = await c.verified_query_service.reject(
        principal.ctx(request.state.trace_id), vq_id, body.note)
    return {"data": vq_payload(vq)}


@router.post("/verified-queries/{vq_id}/archive")
async def archive_verified_query(
    request: Request,
    vq_id: str,
    principal: Principal = Depends(require("semantic.verified_query.update")),
):
    c = _c(request)
    vq = await c.verified_query_service.archive(
        principal.ctx(request.state.trace_id), vq_id)
    return {"data": vq_payload(vq)}

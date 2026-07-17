"""Lineage read endpoint (AC-4/AC-7: model->job, input->job, job->output@version)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import Principal, require

router = APIRouter(prefix="/api/v1")


@router.get("/lineage")
async def get_lineage(
    request: Request,
    urn: str = Query(...),
    direction: str = Query(default="both"),
    principal: Principal = Depends(require("inference.job.read")),
):
    c = request.app.state.container
    ctx = principal.ctx(request.state.trace_id)
    async with c.deps.uow_factory(ctx.tenant_id) as uow:
        edges = await uow.lineage.edges_touching(urn, direction)
    return {
        "data": {
            "urn": urn,
            "edges": [
                {"from": e.from_urn, "to": e.to_urn, "activity": e.activity,
                 "run_urn": e.run_urn}
                for e in edges
            ],
        }
    }

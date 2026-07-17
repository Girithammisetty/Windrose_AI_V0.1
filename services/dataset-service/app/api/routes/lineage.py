"""Lineage graph write/read APIs (DST-FR-040..043)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import EdgeCreate

router = APIRouter(prefix="/api/v1")


@router.post("/lineage/edges", status_code=201)
async def create_edge(
    request: Request,
    response: Response,
    body: EdgeCreate,
    principal: Principal = Depends(require("dataset.lineage.update")),
):
    c = request.app.state.container
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        edge, created = await c.lineage_service.add_edge(ctx, body.model_dump())
        return 201 if created else 200, {
            "data": {
                "id": edge.id,
                "from_urn": edge.from_urn,
                "to_urn": edge.to_urn,
                "activity": edge.activity,
                "run_urn": edge.run_urn,
                "occurred_at": edge.occurred_at.isoformat(),
                "created": created,
            }
        }

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/lineage")
async def query_lineage(
    request: Request,
    urn: str,
    direction: str = "both",
    depth: int | None = None,
    activities: str | None = None,
    principal: Principal = Depends(require("dataset.lineage.read")),
):
    c = request.app.state.container
    settings = c.deps.settings
    result = await c.lineage_service.query(
        principal.ctx(request.state.trace_id),
        urn=urn,
        direction=direction,
        depth=depth if depth is not None else settings.lineage_default_depth,
        activities=activities.split(",") if activities else None,
    )
    return {"data": result}

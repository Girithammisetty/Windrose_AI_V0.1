from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import Principal, require
from app.api.schemas import data
from app.api.serialize import dump

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.get("/gates/{gate_run_id}")
async def get_gate(
    request: Request, gate_run_id: str, principal: Principal = Depends(require("eval.gate.read"))
):
    svc = request.app.state.container.gate_service
    g = await svc.get(_ctx(request, principal), gate_run_id)
    out = dump(g)
    out["candidate"] = {"agent_key": g.agent_key, "content_digest": g.content_digest}
    return data(out)


@router.get("/gates")
async def find_gates(
    request: Request,
    agent_key: str,
    content_digest: str,
    principal: Principal = Depends(require("eval.gate.read")),
):
    svc = request.app.state.container.gate_service
    gates = await svc.find_by_digest(_ctx(request, principal), agent_key, content_digest)
    return data([dump(g) for g in gates])

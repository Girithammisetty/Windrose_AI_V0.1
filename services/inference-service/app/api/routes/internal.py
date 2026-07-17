"""Internal endpoint: the real MCP backend facade tool-plane federates
write-proposal tool execution to (TPL-FR-012, master §2.2-015). Mirrors the
pipeline-orchestrator reference (app/api/routes/internal.py) and case-service's
original Go facade (internal/api/handlers_facade.go): a signed proposal-
execution grant only ever reaches this handler after tool-plane's OPA + grant
verification pipeline, but the backend still re-checks authorization for the
EFFECTIVE HUMAN (obo_sub) against the real authz client before performing the
write — defense in depth, the backend never blindly trusts the gateway.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.auth import Principal, require_internal
from app.domain.errors import AppError

router = APIRouter()


class McpInvokeRequest(BaseModel):
    tool_id: str
    version: str | None = None
    args: dict = {}
    tenant: str
    obo_sub: str | None = None
    agent_id: str | None = None


# tool_id -> the action a caller must hold (mirrors the REST endpoint that
# exposes the same capability to humans, POST /api/v1/inferences -> require
# ("inference.job.create")) — an agent's proposal-execution grant is not a
# permission bypass, just a durable record of a human's APPROVE.
_MCP_TOOL_ACTIONS = {
    "inference.submit": "inference.job.create",
}


def _mcp_output(status: int, output: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content={"output": output})


@router.post("/internal/v1/mcp/invoke")
async def mcp_invoke(request: Request, body: McpInvokeRequest,
                     spiffe: str = Depends(require_internal)):
    """The real MCP backend facade tool-plane federates write-proposal tool
    execution to. Reached only after tool-plane's full enforcement pipeline
    (OPA + a signed proposal-execution grant bound to this exact
    tenant/tool/tier/args digest) — this handler still re-checks authorization
    for the EFFECTIVE HUMAN (obo_sub) against the real authz client, the same
    defense-in-depth case-service's facade applies."""
    c = request.app.state.container
    action = _MCP_TOOL_ACTIONS.get(body.tool_id)
    if action is None:
        return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})

    principal = Principal(
        sub=f"agent:{body.agent_id}" if body.agent_id else "svc:mcp-gateway",
        tenant_id=body.tenant, typ="agent_obo" if body.agent_id else "service",
        agent_id=body.agent_id, obo_sub=body.obo_sub,
        workspace_id=body.args.get("workspace_id"))
    if not await request.app.state.authz.allow(principal, action, None):
        return _mcp_output(403, {"error": f"not allowed: {action}"})

    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    try:
        if body.tool_id == "inference.submit":
            args = body.args
            out = await c.mcp.submit(
                ctx, model_version_urn=args["model_version_urn"],
                input_dataset_urn=args["input_dataset_urn"],
                output_dataset_name=args.get("output_dataset_name"),
                parameters=args.get("parameters"))
        else:
            return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})
    except AppError as exc:
        return _mcp_output(exc.status, {"error": exc.message, "code": exc.code})
    except KeyError as exc:
        return _mcp_output(422, {"error": f"missing required arg {exc}"})
    return {"output": out}

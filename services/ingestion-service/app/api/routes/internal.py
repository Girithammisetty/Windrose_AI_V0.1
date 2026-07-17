"""Internal endpoints: the MCP backend facade tool-plane federates
write-proposal tool execution to (TPL-FR-012, master §2.2-015)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.auth import Principal, require_internal
from app.api.deps import tenant_urn
from app.domain.errors import AppError
from app.mcp.facade import McpFacade

router = APIRouter()


class McpInvokeRequest(BaseModel):
    tool_id: str
    version: str | None = None
    args: dict = {}
    tenant: str
    obo_sub: str | None = None
    agent_id: str | None = None


# tool_id -> the action a caller must hold (mirrors the REST endpoint that
# exposes the same capability to humans — an agent's proposal-execution grant
# is not a permission bypass, just a durable record of a human's APPROVE).
_MCP_TOOL_ACTIONS = {
    "ingestion.create": "ingestion.ingestion.create",
}


def _mcp_output(status: int, output: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content={"output": output})


@router.post("/internal/v1/mcp/invoke")
async def mcp_invoke(request: Request, body: McpInvokeRequest,
                     spiffe: str = Depends(require_internal)):
    """The real MCP backend facade tool-plane federates write-proposal tool
    execution to (TPL-FR-012). Reached only after tool-plane's full enforcement
    pipeline (OPA + a signed proposal-execution grant bound to this exact
    tenant/tool/tier/args digest) — this handler still re-checks authorization
    for the EFFECTIVE HUMAN (obo_sub) against the real OPA sidecar, the same
    defense-in-depth case-service's and pipeline-orchestrator's facades apply:
    the backend never blindly trusts the gateway."""
    container = request.app.state.container
    action = _MCP_TOOL_ACTIONS.get(body.tool_id)
    if action is None:
        return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})

    principal = Principal(
        sub=f"agent:{body.agent_id}" if body.agent_id else "svc:mcp-gateway",
        tenant_id=body.tenant, typ="agent_obo" if body.agent_id else "service",
        agent_id=body.agent_id, obo_sub=body.obo_sub,
        workspace_id=body.args.get("workspace_id"))
    resource_urn = tenant_urn(body.tenant, "ingestion", "*")
    if not await container.policy.allow(principal, action, resource_urn):
        return _mcp_output(403, {"error": f"not allowed: {action}"})

    mcp = McpFacade(container)
    try:
        if body.tool_id == "ingestion.create":
            out = await mcp.create_ingestion(principal, **body.args)
        else:
            return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})
    except AppError as exc:
        return _mcp_output(exc.status, {"error": exc.message, "code": exc.code})
    except KeyError as exc:
        return _mcp_output(422, {"error": f"missing required arg {exc}"})
    except TypeError as exc:
        return _mcp_output(422, {"error": f"invalid args: {exc}"})
    return {"output": out}

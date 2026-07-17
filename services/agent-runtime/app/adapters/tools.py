"""Tool adapter — REAL tool-plane mcp-gateway client (ART-FR-012).

All tool calls go through ``POST /mcp`` (JSON-RPC 2.0, method ``tools/call``).
A write-tier call with no grant returns ``proposal_required`` (isError:false,
structuredContent.status). After human approval the signed grant is presented in
``params._meta.proposal_grant`` — tool-plane verifies signature + binding and, on
success, dispatches to the backend facade and returns the output.
"""

from __future__ import annotations

import httpx

from app.domain.ports import ToolResult


class ToolPlaneClient:
    def __init__(self, base_url: str, *, mcp_path: str = "/mcp",
                 timeout_s: float = 30.0) -> None:
        self._url = base_url.rstrip("/") + mcp_path
        self._timeout = timeout_s
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def call(
        self,
        *,
        tool_id: str,
        arguments: dict,
        tenant_id: str,
        auth_token: str,
        version: str | None = None,
        proposal_grant: str | None = None,
    ) -> ToolResult:
        meta: dict = {}
        if version:
            meta["version"] = version
        if proposal_grant:
            meta["proposal_grant"] = proposal_grant
        params: dict = {"name": tool_id, "arguments": arguments or {}}
        if meta:
            params["_meta"] = meta
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": params,
        }
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        if "error" in body:
            err = body["error"]
            return ToolResult(ok=False, status="error",
                              code=str(err.get("code")), message=err.get("message"))
        result = body.get("result") or {}
        structured = result.get("structuredContent") or {}
        status = structured.get("status")
        if status == "proposal_required":
            return ToolResult(
                ok=False, status="proposal_required", output=structured,
                tier=structured.get("tier"),
                side_effects=structured.get("side_effects"),
                code="PROPOSAL_REQUIRED",
            )
        if result.get("isError"):
            return ToolResult(ok=False, status="error", output=structured,
                              code=structured.get("code"),
                              message=structured.get("message"))
        return ToolResult(ok=True, status="ok", output=structured)

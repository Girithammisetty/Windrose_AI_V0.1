"""Datacern external-agent client (BRD 60 WS5).

A thin, dependency-free (stdlib-only) client a customer drops into their OWN
agent — a LangGraph bot, a Copilot, Claude, anything — so its writes to
Datacern go through the platform's governed rails: every write becomes a
four-eyes proposal that lands in the tamper-evident WORM audit chain and can
only take effect after a distinct human approves it. The agent never writes a
system of record directly; it *proposes*.

Two calls:

    agent = DatacernAgentClient(base_url="https://agent.datacern.example",
                                token="<your agent token>")

    # Discover the tools THIS agent is allowed to use (its declared toolset).
    tools = agent.list_tools(gateway_url="https://mcp.datacern.example")

    # Propose a write. Returns immediately with a PENDING proposal — the write
    # does not happen until a human approves it in the Datacern approval inbox.
    proposal = agent.propose(
        tool_id="case.apply_disposition", tool_version="1.0.0",
        args={"case_id": "c-123", "severity": "high"},
        affected_urns=["wr:<tenant>:case:case/c-123"],
        rationale="Merchant-error pattern; recommend expedited refund.",
    )
    print(proposal.id, proposal.status)  # -> "<uuid>", "pending"

The token is a Datacern *agent* principal (typ=agent_obo or agent_autonomous)
identifying a registered external agent — never a raw user token. Keep it
server-side; never embed it in a browser.

Design notes:
  * stdlib only (urllib) — vendor this single package into any Python project.
  * every write is propose-only by construction on the server; there is no
    "execute" call here, by design.
  * the HTTP transport is injectable (``transport=``) so it is contract-
    testable without a live server.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["DatacernAgentClient", "Proposal", "DatacernAgentError"]

# A transport is (method, url, headers, body_bytes) -> (status, body_bytes).
# Injectable so tests exercise real request-building/parsing without a socket.
Transport = Callable[[str, str, dict, bytes | None], "tuple[int, bytes]"]

# The only tier an external agent may use: proposals a human approves. The
# server enforces this ceiling too (this default just keeps callers honest).
WRITE_PROPOSAL = "write-proposal"


class DatacernAgentError(Exception):
    """A Datacern API error. Carries the platform error envelope's fields so a
    caller can branch on ``code`` (e.g. GUARDRAIL_VIOLATION, PERMISSION_DENIED,
    VALIDATION_FAILED) and cite ``trace_id`` in a support request."""

    def __init__(self, status: int, code: str, message: str, trace_id: str | None = None):
        super().__init__(f"[{status} {code}] {message}")
        self.status = status
        self.code = code
        self.message = message
        self.trace_id = trace_id


@dataclass(frozen=True)
class Proposal:
    """A governed write proposal. ``status`` is ``pending`` until a distinct
    human approves it (four-eyes). The write only takes effect on approval."""

    id: str
    status: str
    tool_id: str
    tier: str
    resource_urn: str | None
    affected_urns: list[str]
    expires_at: str | None
    predicted_effect: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return self.status == "pending"

    @classmethod
    def _from_view(cls, view: dict[str, Any]) -> "Proposal":
        return cls(
            id=view.get("id", ""),
            status=view.get("status", ""),
            tool_id=view.get("tool_id", ""),
            tier=view.get("tier", ""),
            resource_urn=view.get("resource_urn"),
            affected_urns=list(view.get("affected_urns") or []),
            expires_at=view.get("expires_at"),
            predicted_effect=view.get("predicted_effect") or {},
            raw=view,
        )


def _urllib_transport(timeout: float) -> Transport:
    def transport(method: str, url: str, headers: dict, body: bytes | None):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:  # 4xx/5xx still carry a JSON body
            return e.code, e.read()

    return transport


class DatacernAgentClient:
    """Client for the Datacern external-agent governed write ingress."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not token:
            raise ValueError("token is required")
        self._base = base_url.rstrip("/")
        self._token = token
        self._transport = transport or _urllib_transport(timeout)

    # -- public API ---------------------------------------------------------

    def propose(
        self,
        *,
        tool_id: str,
        tool_version: str,
        args: dict[str, Any],
        affected_urns: list[str],
        tier: str = WRITE_PROPOSAL,
        side_effects: str = "reversible",
        rationale: str = "",
        required_action: str | None = None,
        workspace_id: str | None = None,
        predicted_effect: dict[str, Any] | None = None,
    ) -> Proposal:
        """Propose a governed write. Returns a PENDING proposal — the write
        does not happen until a distinct human approves it (four-eyes).

        ``args`` are the tool's arguments (validated against the tool's schema
        server-side). ``affected_urns`` are the resource URNs this write
        touches (the first is treated as the primary resource). ``rationale``
        and ``predicted_effect`` are the agent's own claims — the server
        recomputes the authoritative effect and demotes the agent's version to
        an ``agent_summary`` (anti-laundering), so be truthful but know it is
        not trusted verbatim.
        """
        # Fail fast client-side on the same shape the server requires, so a
        # caller gets a clear local error instead of a round-trip 400.
        if not tool_id or not tool_version:
            raise ValueError("tool_id and tool_version are required")
        if not isinstance(args, dict):
            raise ValueError("args must be a dict")
        if not affected_urns:
            raise ValueError("affected_urns must be a non-empty list")

        body: dict[str, Any] = {
            "tool_id": tool_id,
            "tool_version": tool_version,
            "tier": tier,
            "side_effects": side_effects,
            "args": args,
            "affected_urns": list(affected_urns),
            "rationale": rationale,
        }
        if required_action is not None:
            body["required_action"] = required_action
        if workspace_id is not None:
            body["workspace_id"] = workspace_id
        if predicted_effect is not None:
            body["predicted_effect"] = predicted_effect

        resp = self._request("POST", "/external/v1/intents", body)
        return Proposal._from_view(resp.get("data") or {})

    def list_tools(self, *, gateway_url: str) -> list[dict[str, Any]]:
        """List the tools THIS agent is allowed to call — its declared
        allow-list — by asking the Datacern MCP gateway (real MCP
        ``tools/list``). Requires the gateway URL (a separate host from the
        ingress base). Killed/disabled tools are already filtered out by the
        gateway, so what comes back is what the agent can actually use."""
        if not gateway_url:
            raise ValueError("gateway_url is required to list tools")
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        status, raw = self._transport(
            "POST", gateway_url.rstrip("/") + "/mcp", self._headers(), _dumps(rpc)
        )
        doc = _loads(raw)
        if status >= 400 or "error" in doc:
            err = doc.get("error") or {}
            raise DatacernAgentError(
                status, str(err.get("code", "MCP_ERROR")),
                str(err.get("message", "tools/list failed")))
        return list((doc.get("result") or {}).get("tools") or [])

    # -- internals ----------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "authorization": f"Bearer {self._token}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _request(self, method: str, path: str, body: dict) -> dict:
        status, raw = self._transport(
            method, self._base + path, self._headers(), _dumps(body))
        doc = _loads(raw)
        if status >= 400:
            err = doc.get("error") if isinstance(doc, dict) else None
            if isinstance(err, dict):
                raise DatacernAgentError(
                    status, str(err.get("code", "ERROR")),
                    str(err.get("message", "request failed")),
                    err.get("trace_id"))
            raise DatacernAgentError(status, "ERROR", f"request failed ({status})")
        return doc if isinstance(doc, dict) else {}


def _dumps(v: Any) -> bytes:
    return json.dumps(v).encode("utf-8")


def _loads(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw.decode("utf-8"))
        return v if isinstance(v, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}

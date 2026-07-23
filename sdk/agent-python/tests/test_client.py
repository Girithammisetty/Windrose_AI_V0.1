"""Contract tests for the Datacern external-agent SDK. A real in-process
transport (not a mock of the client's own logic) captures the request the
client actually builds and returns canned server-shaped responses, so
request-building + response-parsing are exercised for real without a socket."""

from __future__ import annotations

import json

import pytest

from datacern_agent import DatacernAgentClient, DatacernAgentError, Proposal


class RecordingTransport:
    """Captures each (method, url, headers, body) and replies from a queue."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, url, headers, body):
        parsed = json.loads(body.decode()) if body else None
        self.calls.append({"method": method, "url": url, "headers": headers, "body": parsed})
        status, doc = self.replies.pop(0)
        return status, json.dumps(doc).encode()


def _client(replies):
    t = RecordingTransport(replies)
    return DatacernAgentClient("https://agent.example", "tok-123", transport=t), t


PROPOSAL_VIEW = {
    "id": "prop-1", "status": "pending", "tool_id": "case.apply_disposition",
    "tier": "write-proposal", "affected_urns": ["wr:t:case:case/c-1"],
    "resource_urn": "wr:t:case:case/c-1", "expires_at": "2026-07-30T00:00:00Z",
    "predicted_effect": {"blast_radius": 1, "reversibility": "reversible"},
}


def test_propose_builds_the_right_request_and_parses_the_proposal():
    client, t = _client([(200, {"data": PROPOSAL_VIEW, "executed": False})])
    p = client.propose(
        tool_id="case.apply_disposition", tool_version="1.0.0",
        args={"case_id": "c-1", "severity": "high"},
        affected_urns=["wr:t:case:case/c-1"],
        rationale="merchant error",
    )
    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://agent.example/external/v1/intents"
    assert call["headers"]["authorization"] == "Bearer tok-123"
    # required fields + the propose-only defaults
    assert call["body"]["tool_id"] == "case.apply_disposition"
    assert call["body"]["tool_version"] == "1.0.0"
    assert call["body"]["tier"] == "write-proposal"
    assert call["body"]["side_effects"] == "reversible"
    assert call["body"]["args"] == {"case_id": "c-1", "severity": "high"}
    assert call["body"]["affected_urns"] == ["wr:t:case:case/c-1"]
    assert call["body"]["rationale"] == "merchant error"
    # optional fields omitted when not given
    assert "workspace_id" not in call["body"] and "required_action" not in call["body"]

    assert isinstance(p, Proposal)
    assert p.id == "prop-1" and p.status == "pending" and p.pending
    assert p.tool_id == "case.apply_disposition"
    assert p.resource_urn == "wr:t:case:case/c-1"
    assert p.predicted_effect["blast_radius"] == 1


def test_propose_passes_optional_fields_when_given():
    client, t = _client([(200, {"data": PROPOSAL_VIEW})])
    client.propose(
        tool_id="x", tool_version="1", args={"a": 1}, affected_urns=["u"],
        required_action="case.case.update", workspace_id="ws-9",
        predicted_effect={"note": "mine"},
    )
    body = t.calls[0]["body"]
    assert body["required_action"] == "case.case.update"
    assert body["workspace_id"] == "ws-9"
    assert body["predicted_effect"] == {"note": "mine"}


def test_propose_validates_client_side_before_any_network():
    client, t = _client([])  # no reply queued: a network call would IndexError
    with pytest.raises(ValueError, match="affected_urns"):
        client.propose(tool_id="x", tool_version="1", args={}, affected_urns=[])
    with pytest.raises(ValueError, match="tool_id and tool_version"):
        client.propose(tool_id="", tool_version="1", args={}, affected_urns=["u"])
    assert t.calls == []  # nothing hit the wire


def test_server_error_envelope_raises_typed_error():
    client, _ = _client([(
        403,
        {"error": {"code": "GUARDRAIL_VIOLATION",
                   "message": "tool not on the agent's allow-list",
                   "trace_id": "tr-9"}},
    )])
    with pytest.raises(DatacernAgentError) as ei:
        client.propose(tool_id="x", tool_version="1", args={"a": 1}, affected_urns=["u"])
    err = ei.value
    assert err.status == 403
    assert err.code == "GUARDRAIL_VIOLATION"
    assert err.trace_id == "tr-9"
    assert "allow-list" in err.message


def test_list_tools_speaks_mcp_and_returns_the_toolset():
    client, t = _client([(
        200,
        {"jsonrpc": "2.0", "id": 1,
         "result": {"tools": [{"name": "case.apply_disposition"}, {"name": "case.assign"}]}},
    )])
    tools = client.list_tools(gateway_url="https://mcp.example")
    call = t.calls[0]
    assert call["url"] == "https://mcp.example/mcp"
    assert call["body"]["method"] == "tools/list"
    assert [x["name"] for x in tools] == ["case.apply_disposition", "case.assign"]


def test_list_tools_requires_gateway_url():
    client, _ = _client([])
    with pytest.raises(ValueError, match="gateway_url"):
        client.list_tools(gateway_url="")


def test_constructor_requires_base_url_and_token():
    with pytest.raises(ValueError):
        DatacernAgentClient("", "tok")
    with pytest.raises(ValueError):
        DatacernAgentClient("https://x", "")

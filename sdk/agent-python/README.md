# datacern-agent

Route your **own** agent's writes through Datacern's governance rails.

A customer's agent — a LangGraph bot, a Copilot, Claude, anything — should not
write your systems of record directly. With this SDK it *proposes* instead:
every write becomes a four-eyes proposal that lands in a tamper-evident WORM
audit chain and only takes effect after a distinct human approves it. You keep
your agent; Datacern makes its actions accountable.

Zero runtime dependencies (stdlib only) — `pip install` it, or vendor the
single `datacern_agent/` package into your project.

## Install

```bash
pip install datacern-agent          # or: copy the datacern_agent/ folder in
```

## Use

```python
from datacern_agent import DatacernAgentClient, DatacernAgentError

agent = DatacernAgentClient(
    base_url="https://agent.datacern.example",   # your Datacern agent-runtime
    token="<your agent token>",                  # a Datacern *agent* principal
)

# Optional: discover the tools THIS agent is allowed to call (its allow-list).
tools = agent.list_tools(gateway_url="https://mcp.datacern.example")

# Propose a write. Returns a PENDING proposal — nothing is written yet.
try:
    proposal = agent.propose(
        tool_id="case.apply_disposition",
        tool_version="1.0.0",
        args={"case_id": "c-123", "severity": "high"},
        affected_urns=["wr:<tenant>:case:case/c-123"],
        rationale="Merchant-error pattern; recommend an expedited refund.",
    )
    print(proposal.id, proposal.status)   # -> "<uuid>", "pending"
except DatacernAgentError as e:
    # e.g. the tool isn't on your agent's allow-list, or is above the
    # write-proposal tier ceiling, or the caller lacks permission.
    print(e.status, e.code, e.message, e.trace_id)
```

The write now sits in the Datacern **approval inbox**. A reviewer approves it
under four-eyes; only then does it execute against the system of record, and the
whole decision — who proposed, who approved, the exact call — is recorded in the
WORM chain. Anyone can pull a tamper-evident **evidence pack** for that decision.

## What the platform enforces (you can't opt out)

- **Propose-only.** External writes can never execute inline — a human must
  approve every one, regardless of tenant auto-execute config.
- **Tier ceiling.** An external agent may only use the `write-proposal` tier;
  anything higher is refused.
- **Declared allow-list.** If your agent is registered with a toolset, it may
  only propose tools on that list.
- **Permission-aware.** When acting on behalf of a user, the agent can only
  propose what that user could do themselves (per-resource grants).
- **Anti-laundering.** Your `rationale`/`predicted_effect` are the *agent's*
  claim; the platform recomputes the authoritative effect server-side and
  records yours as an unverified summary.
- **Dual attribution.** Every audit record carries `via_agent` (which agent
  acted) distinct from `actor` (on whose behalf).

## API

### `DatacernAgentClient(base_url, token, *, timeout=15.0, transport=None)`

`transport` lets you inject an HTTP transport (used by the test suite); leave it
`None` for the built-in urllib transport.

### `.propose(*, tool_id, tool_version, args, affected_urns, tier="write-proposal", side_effects="reversible", rationale="", required_action=None, workspace_id=None, predicted_effect=None) -> Proposal`

Returns a `Proposal` (`.id`, `.status`, `.pending`, `.tool_id`, `.tier`,
`.resource_urn`, `.affected_urns`, `.expires_at`, `.predicted_effect`, `.raw`).
Raises `DatacernAgentError` on a governed refusal or other API error.

### `.list_tools(*, gateway_url) -> list[dict]`

Lists the tools your agent may call, via the Datacern MCP gateway.

## Test

```bash
PYTHONPATH=. python -m pytest tests -q
```

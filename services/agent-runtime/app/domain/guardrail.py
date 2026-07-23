"""BRD 60 WS4 — the request-scoped guardrail enforcement point.

The per-agent security envelope (`TenantAgentConfig.guardrail_policy`, BRD 53
inc2) used to be enforced INLINE inside the persona_copilot graph, so it covered
internal copilot runs but NOT the external-intent ingress — which mints a
proposal directly via `ProposalService.create_from_intent`, never touching the
graph. This module lifts the two *write-relevant* slices of the envelope —
**data-scope containment** and **PII-egress redaction** — into pure functions
applied at the ONE proposal-minting chokepoint, so they bind to every write
proposal regardless of origin (internal graph OR external agent).

The **budget** slice stays where it belongs — a per-LLM-run output-token ceiling
in the graph's reasoning step — because a bare external `propose` spends no model
tokens, so there is nothing to cap at the chokepoint.

Envelope shape::

    {"data_scope": {"workspaces": [uuid], "dataset_urns": [urn]},
     "budget":     {"max_tokens_per_session": int},
     "pii":        {"block_pii_egress": bool, "redact": bool}}

Design stance: absence is permissive (no `data_scope` → RLS is the only wall, as
before; no `pii` flag → no redaction). A DECLARED data-scope is additive to RLS
and never a relaxation — it can only ever DENY, never widen. Enforcement fails
CLOSED: an out-of-scope declared workspace raises before any proposal row exists.
"""

from __future__ import annotations

from typing import Any

from app.domain.errors import GuardrailViolation
from app.domain.redact import redact_text


def allowed_workspaces(policy: dict | None) -> set[str]:
    """The set of workspace ids the agent's data-scope confines it to. Empty set
    means "no data-scope declared" (RLS is the only boundary)."""
    ds = (policy or {}).get("data_scope") or {}
    return {str(w) for w in (ds.get("workspaces") or []) if w}


def workspace_in_scope(policy: dict | None, workspace_id: Any) -> bool:
    """True when `workspace_id` is permitted by the agent's data-scope. An empty
    declared scope permits everything (RLS still applies); a non-empty scope
    permits only its members (and never a null workspace — containment can't be
    proven for a write that declares no workspace)."""
    allowed = allowed_workspaces(policy)
    if not allowed:
        return True
    return workspace_id is not None and str(workspace_id) in allowed


def enforce_data_scope(policy: dict | None, workspace_id: Any, *, agent_key: str = "") -> None:
    """Fail CLOSED (`GuardrailViolation`) when the write's declared workspace is
    outside the agent's data-scope. A no-op when no data-scope is declared."""
    if not allowed_workspaces(policy):
        return
    if not workspace_in_scope(policy, workspace_id):
        raise GuardrailViolation(
            f"agent {agent_key or '?'} is data-scoped to workspaces "
            f"{sorted(allowed_workspaces(policy))} and may not write to "
            f"workspace {workspace_id!r}")


def pii_redaction_on(policy: dict | None) -> bool:
    """Whether the agent's policy requires PII-egress redaction of emitted text."""
    pii = (policy or {}).get("pii") or {}
    return bool(pii.get("block_pii_egress") or pii.get("redact"))


def redact_effect(effect: dict | None) -> dict | None:
    """Return a copy of a predicted_effect with its human-facing text scrubbed of
    common direct identifiers. Covers both the model's own claim
    (`summary`/`agent_summary`) and the server-derived `authoritative_summary`,
    plus quoted citation details."""
    if not effect:
        return effect
    pe = dict(effect)
    for k in ("summary", "agent_summary", "authoritative_summary"):
        if pe.get(k):
            pe[k] = redact_text(str(pe[k]))
    if isinstance(pe.get("citations"), list):
        pe["citations"] = [
            {**c, "detail": redact_text(str(c.get("detail", "")))} if isinstance(c, dict) else c
            for c in pe["citations"]
        ]
    return pe

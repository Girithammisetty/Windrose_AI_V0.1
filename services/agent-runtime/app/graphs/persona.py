"""Role-grounding helpers for agent prompts (ART-FR-040).

The copilot tailors its persona and explanation depth to the INVOKING user's
role so a case adjuster gets a practical, plain answer while a data scientist
gets a more technical one — from the SAME agent, without forking graphs. The
caller context ({roles, capabilities, admin}) is resolved server-side from the
rbac projection and threaded into the run inputs as ``state["caller"]``.

These helpers are relevance-only: they shape wording, never permissions.
Authorization is enforced separately by the OPA caller-gate (proposal service).
"""

from __future__ import annotations

# Coarse tone buckets keyed by the first matching role substring. Kept generic
# (role-shaped, not vertical-hardcoded) so it holds across tenants/verticals.
_TECHNICAL_HINTS = ("model builder", "data user", "data integration",
                    "data scientist", "engineer", "admin")


def caller_persona(caller: dict | None, prompt_params: dict | None) -> str:
    """The persona label to frame the prompt with: the caller's primary role
    when known, else the tenant-configured persona, else a safe default."""
    roles = (caller or {}).get("roles") or []
    if roles:
        return roles[0]
    return (prompt_params or {}).get("persona") or "domain user"


def role_directive(caller: dict | None) -> str:
    """A one-line instruction fragment telling the model to match its depth and
    tone to the caller's role. Empty string when the role is unknown (so the
    prompt stays clean rather than asserting a role we didn't resolve)."""
    roles = (caller or {}).get("roles") or []
    if not roles:
        return ""
    role = roles[0]
    low = role.lower()
    if any(h in low for h in _TECHNICAL_HINTS):
        depth = ("They are a technical user — you may use precise ML/data "
                 "terminology and reference pipelines, metrics, and models directly.")
    else:
        depth = ("They are an operational, non-technical user — explain in plain, "
                 "task-focused language and avoid engineering jargon.")
    return f"The person you are helping has the role \"{role}\". {depth}"

"""rbac-service capabilities adapter — resolves the CALLER's roles/capabilities
so the copilot can ground its persona/tone in the invoking user's role
(ART-FR-040 role-grounding). Best-effort by design: grounding the prompt in the
caller's role is a RELEVANCE aid, not an authorization control (authorization is
enforced independently by the OPA caller-gate in the proposal service), so a
failed lookup degrades to a role-neutral prompt — it never blocks a run.
"""

from __future__ import annotations

import httpx


async def fetch_caller_context(
    rbac_url: str, auth_token: str, *, timeout_s: float = 4.0
) -> dict | None:
    """GET {rbac_url}/api/v1/me/capabilities with the CALLER's own bearer token
    (so the projection returned is the caller's, not the agent's). Returns
    ``{"roles": [...], "capabilities": [...], "admin": bool}`` or None on any
    failure (network/401/parse) — the caller must treat None as "unknown role"
    and fall back to the tenant-level persona."""
    if not auth_token:
        return None
    headers = {"authorization": auth_token if auth_token.lower().startswith("bearer ")
               else f"Bearer {auth_token}"}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(
                f"{rbac_url.rstrip('/')}/api/v1/me/capabilities", headers=headers)
        if resp.status_code != 200:
            return None
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    roles = body.get("roles") or []
    caps = body.get("capabilities") or []
    return {
        "roles": [str(r) for r in roles],
        "capabilities": [str(c) for c in caps],
        "admin": bool(body.get("admin")),
    }

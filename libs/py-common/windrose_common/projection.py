"""Granular authz-projection loader (MASTER-FR-012 / RBC-FR-040).

This mirrors ``libs/go-common/opaclient/projection.go``: it assembles the
``input.projection`` slice OPA's ``windrose.authz_input`` policy evaluates by
reading the *granular* ``perm:*`` projection keys that rbac-service's projector
actually writes (catalog, flags, tenant actions, workspace assignment, resource
grant, tenant meta). Go and Python services thus consume the identical rbac
projection — no separate pre-assembled per-(user,action,workspace) key is
needed.

This module is additive: it does not change the existing single-key
``OpaClient`` path. Callers that want the granular path build the projection
here and pass it to ``OpaClient.decision(projection=...)``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as aioredis

CATALOG_KEY = "perm:catalog:actions"


def urn_hash(urn: str) -> str:
    """Resource-grant key suffix: first 32 hex chars of sha256(urn). Matches
    rbac ``domain.URNHash`` and go-common ``opaclient.URNHash``."""
    return hashlib.sha256(urn.encode()).hexdigest()[:32]


def effective_user(subject: dict[str, Any]) -> str:
    """Whose projection is read: OBO calls resolve to the original user."""
    if subject.get("typ") == "agent_obo" and subject.get("obo_sub"):
        return str(subject["obo_sub"])
    return str(subject.get("id", ""))


async def _get_json(redis: aioredis.Redis, key: str) -> dict[str, Any] | None:
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def load_projection(
    redis: aioredis.Redis,
    *,
    tenant: str,
    subject: dict[str, Any],
    action: str,
    workspace_id: str | None = None,
    resource_urn: str | None = None,
) -> dict[str, Any]:
    """Build the OPA ``input.projection`` from the granular ``perm:*`` keys."""
    user = effective_user(subject)
    ws = workspace_id or ""
    proj: dict[str, Any] = {
        "action_known": False,
        "action_scoped": False,
        "autonomous_enabled": False,
        "flags": {"found": False, "admin": False, "ws_admin": []},
        "tenant_actions": {"found": False, "actions": []},
        "workspace": {"assigned": False, "actions": [], "archived": False},
        "resource": {"found": False, "level": "", "archived": False},
        "workspace_archived_tenant": False,
    }

    # Catalog: action -> workspace_scoped (global key).
    cat = await _get_json(redis, CATALOG_KEY)
    if cat and isinstance(cat.get("actions"), dict):
        actions = cat["actions"]
        if action in actions:
            proj["action_known"] = True
            proj["action_scoped"] = bool(actions[action])

    # Flags.
    flags = await _get_json(redis, f"perm:{tenant}:{user}:flags")
    if flags is not None:
        proj["flags"] = {
            "found": True,
            "admin": bool(flags.get("admin", False)),
            "ws_admin": list(flags.get("ws_admin") or []),
        }

    # Tenant-scoped role actions.
    tact = await _get_json(redis, f"perm:{tenant}:{user}:actions")
    if tact is not None:
        proj["tenant_actions"] = {"found": True, "actions": list(tact.get("actions") or [])}

    # Workspace assignment for the request's workspace.
    if ws:
        wentry = await _get_json(redis, f"perm:{tenant}:{user}:ws:{ws}")
        if wentry is not None and not wentry.get("deleted"):
            proj["workspace"] = {
                "assigned": True,
                "actions": list(wentry.get("actions") or []),
                "archived": bool(wentry.get("archived", False)),
            }
        archived = await _get_json(redis, f"perm:{tenant}:archived_ws")
        if archived and ws in (archived.get("ws") or []):
            proj["workspace_archived_tenant"] = True

    # Resource grant overlay for the request's URN.
    if resource_urn:
        rentry = await _get_json(redis, f"perm:{tenant}:{user}:res:{urn_hash(resource_urn)}")
        if rentry is not None and not rentry.get("deleted"):
            proj["resource"] = {
                "found": True,
                "level": str(rentry.get("level", "")),
                "archived": bool(rentry.get("archived", False)),
            }

    # Tenant meta: autonomous-agent enablement.
    meta = await _get_json(redis, f"perm:{tenant}:meta")
    if meta is not None:
        proj["autonomous_enabled"] = bool(meta.get("autonomous_enabled", False))

    return proj

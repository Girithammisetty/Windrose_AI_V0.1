"""Authorization port (MASTER-FR-012/016).

Runtime calls the real OPA sidecar (OPAPolicyEngine via windrose_common). The
unit tier uses StaticPolicyEngine — an in-memory policy double supporting deny
rules so the authz matrix test can exercise PERMISSION_DENIED paths.
"""

from __future__ import annotations

from typing import Protocol

from app.api.auth import Principal
from app.domain.errors import PermissionDeniedError


class PolicyEngine(Protocol):
    async def allow(self, principal: Principal, action: str, resource_urn: str) -> bool: ...


class StaticPolicyEngine:
    """Allow-all by default; tests register (sub, action) denials."""

    def __init__(self) -> None:
        self.denied: set[tuple[str, str]] = set()

    def deny(self, sub: str, action: str) -> None:
        self.denied.add((sub, action))

    async def allow(self, principal: Principal, action: str, resource_urn: str) -> bool:
        return (principal.sub, action) not in self.denied


class OPAPolicyEngine:
    """Real OPA authorization via the shared ``windrose_common`` client: reads the
    per-request permissions projection from Redis and POSTs it as ``input`` to the
    OPA data API (``windrose.authz_input``), returning the allow/deny decision
    (MASTER-FR-012). Runtime policy engine."""

    def __init__(
        self,
        opa_url: str = "http://localhost:8281",
        *,
        redis_url: str = "redis://localhost:6379/0",
    ) -> None:
        from windrose_common.opaclient import OpaClient
        from windrose_common.redisx import build_redis

        self.opa_url = opa_url
        self._redis = build_redis(redis_url)
        self._client = OpaClient(opa_url)

    async def allow(self, principal: Principal, action: str, resource_urn: str) -> bool:
        from windrose_common.projection import load_projection

        subject = {
            "id": principal.effective_user,
            "typ": principal.typ,
            "scopes": principal.scopes,
            "obo_sub": principal.obo_sub or "",
        }
        workspace_id = getattr(principal, "workspace_id", None)
        # Assemble the projection from the granular rbac ``perm:*`` keys (the same
        # keys go-common reads) and thread the workspace from the JWT claim so
        # workspace-scoped actions satisfy OPA's context check (MASTER-FR-012).
        proj = await load_projection(
            self._redis,
            tenant=principal.tenant_id,
            subject=subject,
            action=action,
            workspace_id=workspace_id,
            resource_urn=resource_urn,
        )
        return await self._client.allow(
            subject=subject,
            action=action,
            tenant=principal.tenant_id,
            resource_urn=resource_urn,
            workspace_id=workspace_id,
            projection=proj,
        )


async def authorize(
    policy: PolicyEngine, principal: Principal, action: str, resource_urn: str
) -> None:
    if not await policy.allow(principal, action, resource_urn):
        raise PermissionDeniedError(f"action {action} denied", details={"action": action})

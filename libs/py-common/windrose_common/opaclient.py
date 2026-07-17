"""Real OPA authorization client (MASTER-FR-012).

Mirrors the libs/go-common opaclient model: the caller reads the per-request
permissions *projection* slice from Redis (written by rbac-service's CDC
projector) and POSTs it, together with the request context, as ``input`` to the
OPA data API. OPA evaluates ``windrose.authz_input`` (the input-projection
variant of the canonical policy) and returns the allow/deny decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .redisx import RedisProjection

DEFAULT_PACKAGE = "windrose/authz_input"


def projection_key(tenant_id: str, subject_id: str, action: str, workspace_id: str = "") -> str:
    return f"authz:proj:{tenant_id}:{subject_id}:{action}:{workspace_id}"


@dataclass(slots=True)
class Decision:
    allow: bool
    reason: str
    miss: bool


class OpaClient:
    def __init__(
        self,
        opa_url: str = "http://localhost:8281",
        *,
        package: str = DEFAULT_PACKAGE,
        projection: RedisProjection | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.opa_url = opa_url.rstrip("/")
        self.package = package.strip("/")
        self.projection = projection
        self.timeout_s = timeout_s

    def _build_input(
        self,
        *,
        subject: dict,
        action: str,
        tenant: str,
        resource_urn: str | None,
        workspace_id: str | None,
        projection: dict,
    ) -> dict:
        payload = {
            "subject": subject,
            "action": action,
            "tenant": tenant,
            "projection": projection,
        }
        if resource_urn:
            payload["resource_urn"] = resource_urn
        if workspace_id:
            payload["workspace_id"] = workspace_id
        return payload

    async def _load_projection(
        self, tenant: str, subject: dict, action: str, workspace_id: str | None
    ) -> dict:
        if self.projection is None:
            return {}
        key = projection_key(tenant, subject.get("id", ""), action, workspace_id or "")
        return await self.projection.get(key) or {}

    async def decision(
        self,
        *,
        subject: dict,
        action: str,
        tenant: str,
        resource_urn: str | None = None,
        workspace_id: str | None = None,
        projection: dict | None = None,
    ) -> Decision:
        proj = projection
        if proj is None:
            proj = await self._load_projection(tenant, subject, action, workspace_id)
        opa_input = self._build_input(
            subject=subject,
            action=action,
            tenant=tenant,
            resource_urn=resource_urn,
            workspace_id=workspace_id,
            projection=proj,
        )
        url = f"{self.opa_url}/v1/data/{self.package}/result"
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, json={"input": opa_input})
            resp.raise_for_status()
        result = resp.json().get("result") or {}
        return Decision(
            allow=bool(result.get("allow", False)),
            reason=str(result.get("reason", "deny_default")),
            miss=bool(result.get("miss", False)),
        )

    async def allow(
        self,
        *,
        subject: dict,
        action: str,
        tenant: str,
        resource_urn: str | None = None,
        workspace_id: str | None = None,
        projection: dict | None = None,
    ) -> bool:
        decision = await self.decision(
            subject=subject,
            action=action,
            tenant=tenant,
            resource_urn=resource_urn,
            workspace_id=workspace_id,
            projection=projection,
        )
        return decision.allow

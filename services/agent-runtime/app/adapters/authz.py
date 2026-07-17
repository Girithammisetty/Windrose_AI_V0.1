"""OPA authorization adapter (ART-FR-044, AC-12): real OPA sidecar via the shared
windrose_common client. Approver eligibility = the decision actor must hold the
underlying action's permission on every affected URN."""

from __future__ import annotations

from windrose_common.opaclient import OpaClient
from windrose_common.redisx import RedisProjection, build_redis


class OpaAuthz:
    def __init__(self, opa_url: str, *, redis_url: str, package: str) -> None:
        self._redis = build_redis(redis_url)
        projection = RedisProjection(self._redis)
        self._opa = OpaClient(opa_url, package=package, projection=projection)

    async def allow(
        self,
        *,
        subject: dict,
        action: str,
        tenant: str,
        resource_urn: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        return await self._opa.allow(
            subject=subject, action=action, tenant=tenant,
            resource_urn=resource_urn, workspace_id=workspace_id,
        )

    async def aclose(self) -> None:
        await self._redis.aclose()


class AllowAllAuthz:
    """Unit-tier double: allows everything. Never wired from app.main."""

    async def allow(self, **_kw) -> bool:
        return True


class DenyURNAuthz:
    """Unit-tier double: denies a configured (subject,urn) set for AC-12 tests."""

    def __init__(self, denied: set[tuple[str, str]] | None = None) -> None:
        self._denied = denied or set()

    async def allow(self, *, subject, action, tenant, resource_urn=None, **_kw) -> bool:
        return (subject.get("id"), resource_urn) not in self._denied

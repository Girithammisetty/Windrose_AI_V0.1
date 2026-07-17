"""Workspace membership (BR-10, AC-9): validated at RETRIEVAL time against the
rbac projection so a user removed from a workspace immediately loses retrieval.

``RedisMembershipChecker`` reads the rbac-service Redis projection set
``rbac:ws_member:{tenant}:{user}`` (5s freshness SLO). ``InMemoryMembership`` is
the unit-tier double.
"""

from __future__ import annotations


class RedisMembershipChecker:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)

    @staticmethod
    def _key(tenant_id: str, user_id: str) -> str:
        return f"rbac:ws_member:{tenant_id}:{user_id}"

    async def is_member(self, tenant_id: str, user_id: str, workspace_id: str) -> bool:
        return bool(await self._redis.sismember(self._key(tenant_id, user_id), workspace_id))

    async def aclose(self) -> None:
        await self._redis.aclose()


class InMemoryMembership:
    """Unit-tier double. ``grant`` seeds membership; default-open unless any
    grant exists for the (tenant,user) — mirrors 'no projection => deny' only
    when explicitly seeded."""

    def __init__(self, *, default_allow: bool = True):
        self._members: dict[tuple[str, str], set[str]] = {}
        self._default_allow = default_allow

    def grant(self, tenant_id: str, user_id: str, workspace_id: str) -> None:
        self._members.setdefault((tenant_id, user_id), set()).add(workspace_id)

    def revoke(self, tenant_id: str, user_id: str, workspace_id: str) -> None:
        self._members.get((tenant_id, user_id), set()).discard(workspace_id)

    async def is_member(self, tenant_id: str, user_id: str, workspace_id: str) -> bool:
        key = (tenant_id, user_id)
        if key not in self._members:
            return self._default_allow
        return workspace_id in self._members[key]

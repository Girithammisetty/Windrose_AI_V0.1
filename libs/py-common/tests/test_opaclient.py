"""Real OPA + Redis integration: the client reads a permissions projection slice
from Redis and POSTs it as `input` to the real OPA data API, which evaluates the
rbac `windrose.authz_input` policy bundle loaded into the dev OPA container."""

from __future__ import annotations

import pytest

from windrose_common.opaclient import OpaClient, projection_key
from windrose_common.redisx import RedisProjection, build_redis

pytestmark = pytest.mark.integration


def _admin_projection() -> dict:
    return {
        "action_known": True,
        "action_scoped": False,
        "autonomous_enabled": False,
        "flags": {"found": True, "admin": True, "ws_admin": []},
        "tenant_actions": {"found": False, "actions": []},
        "workspace": {"assigned": False, "actions": [], "archived": False},
        "resource": {"found": False, "level": "", "archived": False},
        "workspace_archived_tenant": False,
    }


async def test_allow_reads_projection_from_redis_and_evaluates_opa(opa, unique):
    redis = build_redis()
    projection = RedisProjection(redis)
    client = OpaClient(projection=projection)
    tenant = f"tenant-{unique}"
    subject = {"id": "user-1", "typ": "user", "scopes": []}
    action = "dataset.read"

    try:
        # admin projection stored in Redis -> allow
        key = projection_key(tenant, "user-1", action, "")
        await projection.put(key, _admin_projection())
        decision = await client.decision(
            subject=subject, action=action, tenant=tenant
        )
        assert decision.allow is True
        assert decision.reason == "allowed"

        # no projection for a different action -> deny (unknown action / miss)
        deny = await client.decision(
            subject=subject, action="dataset.delete", tenant=tenant
        )
        assert deny.allow is False
    finally:
        await redis.aclose()


async def test_explicit_projection_denies_without_grant(opa, unique):
    client = OpaClient()  # no Redis; pass projection explicitly
    empty = {
        "action_known": True,
        "action_scoped": False,
        "autonomous_enabled": False,
        "flags": {"found": False, "admin": False, "ws_admin": []},
        "tenant_actions": {"found": False, "actions": []},
        "workspace": {"assigned": False, "actions": [], "archived": False},
        "resource": {"found": False, "level": "", "archived": False},
        "workspace_archived_tenant": False,
    }
    allowed = await client.allow(
        subject={"id": "u", "typ": "user", "scopes": []},
        action="dataset.read",
        tenant=f"tenant-{unique}",
        projection=empty,
    )
    assert allowed is False

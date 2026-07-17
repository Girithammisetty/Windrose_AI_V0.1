"""Real OPA authz (MASTER-FR-012): the OpaAuthzClient reads the per-request
permissions projection from real Redis and POSTs it to the real OPA sidecar
(windrose.authz_input); allow/deny decisions come from the live Rego bundle."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import TENANT_A, USER_A
from tests.integration.conftest import OPA_URL, REDIS_URL

pytestmark = pytest.mark.integration

ACTION = "memory.memory.create"


def _opa_up() -> bool:
    try:
        return httpx.get(f"{OPA_URL}/health", timeout=3).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _redis_up() -> bool:
    try:
        import redis

        redis.from_url(REDIS_URL).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


class _P:
    def __init__(self):
        self.sub = USER_A
        self.effective_user = USER_A
        self.tenant_id = TENANT_A
        self.typ = "user"
        self.scopes: list[str] = []
        self.obo_sub = None


async def test_real_opa_allow_and_deny(pg):
    if not _opa_up():
        pytest.skip(f"OPA unreachable at {OPA_URL}")
    if not _redis_up():
        pytest.skip(f"Redis unreachable at {REDIS_URL}")

    from windrose_common.opaclient import projection_key
    from windrose_common.redisx import RedisProjection, build_redis

    from app.api.auth import OpaAuthzClient

    redis = build_redis(REDIS_URL)
    proj = RedisProjection(redis)
    # tenant-scoped action grant for the user (projection the rbac projector writes)
    allow_projection = {
        "action_known": True, "action_scoped": False, "autonomous_enabled": False,
        "flags": {"found": True, "admin": False, "ws_admin": []},
        "tenant_actions": {"found": True, "actions": [ACTION]},
        "workspace": {"assigned": False, "actions": [], "archived": False},
        "resource": {"found": False, "level": "", "archived": False},
        "workspace_archived_tenant": False,
    }
    await proj.put(projection_key(TENANT_A, USER_A, ACTION, ""), allow_projection)

    authz = OpaAuthzClient(OPA_URL, redis_url=REDIS_URL)
    try:
        assert await authz.allow(_P(), ACTION, None) is True
        # a different user with no projection entry is denied
        class _Q(_P):
            def __init__(self):
                super().__init__()
                self.sub = "nobody"
                self.effective_user = "nobody"
        assert await authz.allow(_Q(), ACTION, None) is False
    finally:
        await authz.aclose()
        await redis.aclose()

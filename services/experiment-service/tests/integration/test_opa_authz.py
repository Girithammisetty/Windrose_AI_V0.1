"""Integration (real OPA + Redis): the OpaAuthzClient reads the per-request
permissions projection from real Redis and POSTs it to the real OPA sidecar
(windrose.authz_input); allow/deny come from the live Rego bundle
(MASTER-FR-012)."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import TENANT_A
from tests.integration.conftest import OPA_URL, REDIS_URL

pytestmark = pytest.mark.integration

ACTION = "experiment.model.promote"


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
    def __init__(self, sub: str):
        self.sub = sub
        self.effective_user = sub
        self.tenant_id = TENANT_A
        self.typ = "user"
        self.scopes: list[str] = []
        self.obo_sub = None
        self.workspace_id = None


async def test_real_opa_allow_and_deny():
    if not _opa_up():
        pytest.skip(f"OPA unreachable at {OPA_URL}")
    if not _redis_up():
        pytest.skip(f"Redis unreachable at {REDIS_URL}")

    from windrose_common.redisx import RedisProjection, build_redis

    from app.api.auth import OpaAuthzClient

    redis = build_redis(REDIS_URL)
    proj = RedisProjection(redis)
    # Seed the GRANULAR perm:* projection keys that rbac's projector actually
    # writes and that OpaAuthzClient.load_projection assembles (the real runtime
    # path): a known action + a tenant-scoped grant for the "granted" user.
    # perm:catalog:actions is a shared global on the dev-stack Redis — merge our
    # action into whatever a live rbac has projected, never replace the key.
    catalog = await proj.get("perm:catalog:actions") or {}
    catalog.setdefault("actions", {})[ACTION] = False
    await proj.put("perm:catalog:actions", catalog)
    await proj.put(f"perm:{TENANT_A}:granted:flags", {"admin": False, "ws_admin": []})
    await proj.put(f"perm:{TENANT_A}:granted:actions", {"actions": [ACTION]})

    authz = OpaAuthzClient(OPA_URL, redis_url=REDIS_URL)
    try:
        assert await authz.allow(_P("granted"), ACTION, None) is True
        assert await authz.allow(_P("nobody"), ACTION, None) is False
    finally:
        await authz.aclose()
        await redis.aclose()

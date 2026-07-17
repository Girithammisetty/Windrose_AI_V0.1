"""Unit test for the granular authz-projection loader (RBC-FR-040 / MASTER-FR-012).

Verifies that ``load_projection`` assembles the ``input.projection`` OPA expects
from the granular ``perm:*`` keys rbac writes — the same key scheme go-common's
loader reads — so Python and Go services consume the identical rbac projection.
No infra: a tiny in-memory Redis double backs the reads.
"""

from __future__ import annotations

import json

from windrose_common.projection import load_projection, urn_hash


class FakeRedis:
    def __init__(self, data: dict[str, str]):
        self._data = data

    async def get(self, key: str):
        return self._data.get(key)


TENANT = "t-1"
USER = "u-1"
WS = "ws-9"


async def test_admin_workspace_scoped_projection_assembled():
    urn = f"wr:{TENANT}:case:case/abc"
    data = {
        "perm:catalog:actions": json.dumps({"actions": {"case.disposition.approve": True}}),
        f"perm:{TENANT}:{USER}:flags": json.dumps({"admin": True, "ws_admin": [WS]}),
        f"perm:{TENANT}:{USER}:actions": json.dumps({"actions": ["*"]}),
        f"perm:{TENANT}:{USER}:ws:{WS}": json.dumps(
            {"actions": ["*"], "archived": False, "deleted": False}
        ),
        f"perm:{TENANT}:{USER}:res:{urn_hash(urn)}": json.dumps(
            {"level": "owner", "archived": False, "deleted": False}
        ),
        f"perm:{TENANT}:meta": json.dumps({"autonomous_enabled": True}),
    }
    proj = await load_projection(
        FakeRedis(data),
        tenant=TENANT,
        subject={"id": USER, "typ": "user"},
        action="case.disposition.approve",
        workspace_id=WS,
        resource_urn=urn,
    )
    assert proj["action_known"] is True
    assert proj["action_scoped"] is True
    assert proj["flags"] == {"found": True, "admin": True, "ws_admin": [WS]}
    assert proj["tenant_actions"]["found"] is True
    assert proj["workspace"] == {"assigned": True, "actions": ["*"], "archived": False}
    assert proj["resource"] == {"found": True, "level": "owner", "archived": False}
    assert proj["autonomous_enabled"] is True


async def test_unknown_action_and_obo_effective_user():
    # OBO resolves to obo_sub for the projection lookup; unknown action => not known.
    data = {
        "perm:catalog:actions": json.dumps({"actions": {"case.case.read": True}}),
        f"perm:{TENANT}:{USER}:flags": json.dumps({"admin": True, "ws_admin": []}),
    }
    proj = await load_projection(
        FakeRedis(data),
        tenant=TENANT,
        subject={"id": "agent:x@1", "typ": "agent_obo", "obo_sub": USER},
        action="case.case.delete",  # not in catalog
        workspace_id="",
    )
    assert proj["action_known"] is False
    # flags were read for the effective (obo) user, not the agent id
    assert proj["flags"]["admin"] is True


async def test_missing_keys_default_to_deny_shape():
    proj = await load_projection(
        FakeRedis({}),
        tenant=TENANT,
        subject={"id": USER, "typ": "user"},
        action="case.case.read",
    )
    assert proj["action_known"] is False
    assert proj["flags"]["found"] is False
    assert proj["tenant_actions"]["found"] is False
    assert proj["workspace"]["assigned"] is False

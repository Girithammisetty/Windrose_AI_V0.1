"""Kill-switch RLS regression (ART-FR-073 admin surface).

Pins the real bug found while wiring the Tier-1 kill-switch admin UI: 0004 put
``kill_switches`` under FORCE RLS (``tenant_id IS NULL OR tenant_id = GUC``),
but ``create_kill_switch``/``get_kill_switch``/``deactivate_kill_switch``
still ran on a ``_plain()`` session that never sets ``app.tenant_id`` — so
creating the DEFAULT (tenant-scoped, ``agent_version_tenant``) kill switch
raised ``InsufficientPrivilegeError`` under the real non-superuser
``agent_runtime_app`` role every time. Fixed by tenant-scoping create() and by
giving get/deactivate/list a privileged (BYPASSRLS) session for by-id/cross-
tenant control-plane ops (see app/store/sql.py ``_admin()``).
"""

from __future__ import annotations

import pytest

from app.domain.entities import KillSwitch, new_uuid
from app.store.sql import SqlStore
from tests.conftest import TENANT_A, TENANT_B

pytestmark = pytest.mark.integration


def _kill(tenant_id: str | None, reason: str) -> KillSwitch:
    return KillSwitch(kill_id=new_uuid(), scope="agent_version_tenant" if tenant_id else "agent",
                       agent_key="case-triage", version=None, tenant_id=tenant_id,
                       active=True, reason=reason, set_by="user:tester")


async def test_create_tenant_scoped_kill_switch_does_not_raise_rls_violation(
    app_session_factory, super_session_factory
):
    """Regression: this used to raise InsufficientPrivilegeError under the
    non-privileged agent_runtime_app role for the DEFAULT (tenant-scoped) kill
    scope — the exact request shape POST /kill-switches sends by default."""
    store = SqlStore(app_session_factory, super_session_factory)
    ks = _kill(TENANT_A, "test-create-tenant-scoped")
    await store.create_kill_switch(ks)  # must not raise

    fetched = await store.get_kill_switch(ks.kill_id)
    assert fetched is not None
    assert fetched.tenant_id == TENANT_A
    assert fetched.active is True
    assert fetched.created_at is not None


async def test_get_and_deactivate_kill_switch_by_id_across_tenants(
    app_session_factory, super_session_factory
):
    """get/deactivate operate by opaque kill_id without knowing the tenant ahead
    of time (mirrors the DELETE /kill-switches/{id} route) — must work for a
    tenant-scoped row via the privileged _admin() session."""
    store = SqlStore(app_session_factory, super_session_factory)
    ks = _kill(TENANT_B, "test-get-deactivate")
    await store.create_kill_switch(ks)

    assert (await store.get_kill_switch(ks.kill_id)).active is True
    await store.deactivate_kill_switch(ks.kill_id)
    refetched = await store.get_kill_switch(ks.kill_id)
    assert refetched.active is False


async def test_list_kill_switches_tenant_scope_sees_own_and_global_only(
    app_session_factory, super_session_factory
):
    """A tenant admin's list call (tenant_id set) is a REAL RLS session — it
    must see its own tenant's kills + platform-global kills, but NOT another
    tenant's kill switches (the actual security value of the RLS policy)."""
    store = SqlStore(app_session_factory, super_session_factory)
    mine = _kill(TENANT_A, "test-list-own")
    other = _kill(TENANT_B, "test-list-other")
    glob = _kill(None, "test-list-global")
    await store.create_kill_switch(mine)
    await store.create_kill_switch(other)
    await store.create_kill_switch(glob)

    visible = await store.list_kill_switches(TENANT_A)
    ids = {k.kill_id for k in visible}
    assert mine.kill_id in ids
    assert glob.kill_id in ids
    assert other.kill_id not in ids


async def test_list_kill_switches_operator_scope_sees_every_tenant(
    app_session_factory, super_session_factory
):
    """An operator's list call (tenant_id=None) uses the privileged _admin()
    session — it must see every tenant's active kills, which is the whole
    point of the platform kill-switch dashboard (a single tenant's RLS-scoped
    session structurally cannot provide this view)."""
    store = SqlStore(app_session_factory, super_session_factory)
    mine = _kill(TENANT_A, "test-list-op-a")
    other = _kill(TENANT_B, "test-list-op-b")
    await store.create_kill_switch(mine)
    await store.create_kill_switch(other)

    visible = await store.list_kill_switches(None)
    ids = {k.kill_id for k in visible}
    assert mine.kill_id in ids
    assert other.kill_id in ids


async def test_list_kill_switches_excludes_deactivated(app_session_factory, super_session_factory):
    store = SqlStore(app_session_factory, super_session_factory)
    ks = _kill(TENANT_A, "test-list-excludes-lifted")
    await store.create_kill_switch(ks)
    await store.deactivate_kill_switch(ks.kill_id)

    visible = await store.list_kill_switches(TENANT_A)
    assert ks.kill_id not in {k.kill_id for k in visible}

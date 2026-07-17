"""RLS tenant isolation at the Postgres layer (MASTER-FR-001/004, AC-10).
Rows written under tenant A are invisible to a tenant-B unit of work."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.entities import CallCtx
from tests.conftest import TENANT_A, TENANT_B, WORKSPACE, make_settings

pytestmark = pytest.mark.integration


async def test_cross_tenant_template_invisible_under_rls(app_sf, clock):
    c = build_container(make_settings(), mode="sql", session_factory=app_sf, clock=clock)
    ctx_a = CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "a"},
                    workspace_id=WORKSPACE)
    template, _ = await c.template_service.create(ctx_a, {
        "workspace_id": WORKSPACE, "name": "a-secret", "pipeline_type": "data_prep",
        "definition": __import__("tests.conftest", fromlist=["data_prep_definition"])
        .data_prep_definition()})

    # Same row id, tenant-B unit of work → RLS hides it.
    async with c.deps.uow_factory(TENANT_B) as uow:
        assert await uow.runs.get(template.id) is None
        assert await uow.templates.get(template.id) is None
    # Tenant A still sees it.
    async with c.deps.uow_factory(TENANT_A) as uow:
        assert (await uow.templates.get(template.id)) is not None


async def test_shipped_default_role_forces_rls_and_isolates(pg, clock):
    """Boot with the SHIPPED DEFAULT runtime role (config.py database_url →
    pipeline_app) and prove it is non-owner, non-superuser, FORCE-RLS-bound, and
    that a cross-tenant read returns nothing (the deployable default is safe)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    default_engine = create_async_engine(pg["default_url"], pool_pre_ping=True)
    sf = async_sessionmaker(default_engine, expire_on_commit=False)
    try:
        c = build_container(make_settings(), mode="sql", session_factory=sf, clock=clock)
        ctx_a = CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "a"},
                        workspace_id=WORKSPACE)
        from tests.conftest import data_prep_definition

        template, _ = await c.template_service.create(ctx_a, {
            "workspace_id": WORKSPACE, "name": "default-role-secret",
            "pipeline_type": "data_prep", "definition": data_prep_definition()})

        # The shipped default role must be a non-owner, non-superuser.
        async with sf() as s:
            role = (await s.execute(text("SELECT current_user"))).scalar_one()
            is_super = (await s.execute(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
            ).scalar_one()
            owner = (await s.execute(text(
                "SELECT tableowner FROM pg_tables WHERE tablename = 'pipeline_templates'"))
            ).scalar_one()
        assert role == "pipeline_app"
        assert is_super is False
        assert owner != "pipeline_app"  # non-owner → FORCE RLS is what protects it

        # Cross-tenant read under the default role returns nothing (raw SQL + repo).
        async with sf() as s:
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": TENANT_B})
            rows = (await s.execute(text(
                "SELECT count(*) FROM pipeline_templates"))).scalar_one()
        assert rows == 0
        async with c.deps.uow_factory(TENANT_B) as uow:
            assert await uow.templates.get(template.id) is None
        async with c.deps.uow_factory(TENANT_A) as uow:
            assert await uow.templates.get(template.id) is not None
    finally:
        await default_engine.dispose()


async def test_labeled_examples_are_tenant_scoped(app_sf, clock):
    from app.events.envelope import make_envelope

    c = build_container(make_settings(), mode="sql", session_factory=app_sf, clock=clock)
    urn = "wr:t:dataset:dataset/shared"
    env = make_envelope(
        event_type="case.disposition_applied", tenant_id=TENANT_A,
        actor={"type": "user", "id": "x"}, resource_urn="wr:t:case:case/1",
        payload={"dataset_urn": urn, "row_pk": "r1",
                 "disposition": {"category": "fraud"}, "features": {"amount": 10}})
    await c.consumer.handle(env)

    async with c.deps.uow_factory(TENANT_A) as uow:
        assert await uow.labeled_examples.count_for_dataset(urn) == 1
    async with c.deps.uow_factory(TENANT_B) as uow:
        assert await uow.labeled_examples.count_for_dataset(urn) == 0

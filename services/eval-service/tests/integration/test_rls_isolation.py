"""RLS tenant isolation + real Postgres persistence (MASTER-FR-001, AC-14)."""

from __future__ import annotations

import pytest

from app.domain.entities import CallCtx
from tests.conftest import TENANT_A, TENANT_B, auth

pytestmark = pytest.mark.integration


def ctx(tenant):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": "u"})


async def test_case_persisted_and_rls_isolates_tenants(container):
    cs = container.case_service
    c = await cs.create(
        ctx(TENANT_A),
        {
            "dataset_key": "analytics/nl2sql",
            "agent_key": "analytics",
            "input": {"messages": []},
            "expected": {"kind": "rubric", "value": {}},
            "status": "active",
        },
    )
    # same tenant reads it back from Postgres
    got = await cs.get(ctx(TENANT_A), c.id)
    assert got.id == c.id
    # tenant B cannot see it — RLS makes the row invisible (NotFound -> 404)
    from app.domain.errors import NotFound

    with pytest.raises(NotFound):
        await cs.get(ctx(TENANT_B), c.id)


async def test_full_ci_gate_persists_to_postgres(container):
    # seed fixture + active sql case + suite, run the CI gate, verify persistence.
    container.warehouse.seed(
        "fw", {"orders": (["region", "rev"], [("EMEA", 100.0), ("AMER", 200.0)])}
    )
    cs = container.case_service
    c = await cs.create(
        ctx(TENANT_A),
        {
            "dataset_key": "analytics/nl2sql",
            "agent_key": "analytics",
            "input": {
                "messages": [{"role": "user", "content": "rev by region"}],
                "context_refs": {"fixture_warehouse": "fw"},
            },
            "expected": {
                "kind": "sql_result",
                "value": {
                    "sql": "SELECT region, SUM(rev) FROM orders GROUP BY region",
                    "order_insensitive": True,
                },
            },
            "status": "active",
        },
    )
    await container.suite_service.create(
        ctx(TENANT_A),
        {
            "suite_id": "analytics-gate",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [
                {
                    "scorer": "sql_result_equivalence",
                    "version": 2,
                    "weight": 1.0,
                    "regression_threshold": -0.02,
                }
            ],
            "gate_rule": "sql_result_equivalence.pass_rate >= 0.99",
            "min_cases": 1,
        },
    )
    outputs = {c.id: {"sql": "SELECT region, SUM(rev) FROM orders GROUP BY region"}}
    run = await container.run_service.create_and_execute(
        ctx(TENANT_A),
        trigger="ci",
        agent_key="analytics",
        candidate={"content_digest": "sha256:persist"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
    )
    assert run.status == "completed"
    gate = await container.gate_service.evaluate_from_run(ctx(TENANT_A), run.id)
    assert gate.gate_passed is True
    # re-read the gate from Postgres by its addressable id
    reread = await container.gate_service.get(ctx(TENANT_A), gate.gate_run_id)
    assert reread.content_digest == "sha256:persist"
    # tenant B cannot see the gate (RLS)
    from app.domain.errors import NotFound

    with pytest.raises(NotFound):
        await container.gate_service.get(ctx(TENANT_B), gate.gate_run_id)


async def test_shipped_default_role_rls_isolation(default_container, default_engine):
    """FORCE ROW LEVEL SECURITY + the shipped-default non-owner role (eval_app_rt)
    isolate tenants — proven with the exact role the default DSN uses, not the
    test-only eval_rt role (AC-14, HIGH-equiv systemic finding)."""
    from sqlalchemy import text

    cs = default_container.case_service
    c = await cs.create(ctx(TENANT_A), {
        "dataset_key": "k/x", "agent_key": "a", "input": {},
        "expected": {"kind": "rubric", "value": {}}, "status": "active"})
    assert (await cs.get(ctx(TENANT_A), c.id)).id == c.id
    from app.domain.errors import NotFound
    with pytest.raises(NotFound):
        await cs.get(ctx(TENANT_B), c.id)

    # raw check as eval_app_rt: with tenant B context (and with no context) the
    # row is invisible even though it exists for tenant A — FORCE RLS enforced.
    async with default_engine.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                           {"t": TENANT_B})
        n_b = (await conn.execute(
            text("SELECT count(*) FROM eval_cases WHERE id = :i"), {"i": c.id})).scalar()
        await conn.execute(text("SELECT set_config('app.tenant_id', '', false)"))
        n_none = (await conn.execute(
            text("SELECT count(*) FROM eval_cases WHERE id = :i"), {"i": c.id})).scalar()
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                           {"t": TENANT_A})
        n_a = (await conn.execute(
            text("SELECT count(*) FROM eval_cases WHERE id = :i"), {"i": c.id})).scalar()
    assert n_b == 0 and n_none == 0 and n_a == 1


async def test_frozen_dataset_case_mutation_blocked_by_db_trigger(container, engine):
    """AC-15 defense-in-depth: a raw SQL mutation of a case in a frozen dataset
    version is rejected by the DB trigger, bypassing the app layer entirely."""
    import sqlalchemy.exc
    from sqlalchemy import text

    cs = container.case_service
    ds = container.dataset_service
    await ds.create(ctx(TENANT_A), {"dataset_key": "frz/x", "agent_key": "a"})
    c = await cs.create(ctx(TENANT_A), {
        "dataset_key": "frz/x", "agent_key": "a", "input": {},
        "expected": {"kind": "rubric", "value": {}}, "status": "active"})
    await ds.freeze(ctx(TENANT_A), "frz/x", 1)

    with pytest.raises(sqlalchemy.exc.DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                               {"t": TENANT_A})
            await conn.execute(
                text("UPDATE eval_cases SET weight = 9.0 WHERE id = :i"), {"i": c.id})


async def test_api_cross_tenant_404(client):
    created = await client.post(
        "/api/v1/cases",
        json={
            "dataset_key": "k/x",
            "agent_key": "a",
            "input": {},
            "expected": {"kind": "rubric", "value": {}},
        },
        headers=auth(TENANT_A),
    )
    assert created.status_code == 201
    case_id = created.json()["data"]["id"]
    other = await client.get(f"/api/v1/cases/{case_id}", headers=auth(TENANT_B))
    assert other.status_code == 404

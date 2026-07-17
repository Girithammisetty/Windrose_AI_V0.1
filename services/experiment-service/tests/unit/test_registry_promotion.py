"""EXP-FR-030..036, AC-6/7/8/9/10: registration + governed promotion gate."""

from __future__ import annotations

import pytest

from app.domain.errors import (
    Conflict,
    RunNotFinished,
    SelfApprovalForbidden,
    ValidationFailed,
)
from tests.conftest import ctx_for, make_experiment, seed_finished_run


async def _register(container, ctx, *, name="fraud-model", run_metrics=None):
    exp = await make_experiment(container, ctx, name=f"exp-{name}")
    run = await seed_finished_run(container, ctx, exp.id, mlflow_run_id=f"run-{name}",
                                  metrics=run_metrics or {"f1_score": 0.9},
                                  params={"max_depth": "6"})
    result = await container.registry_service.register(ctx, exp.id, run.id,
                                                       {"model_name": name})
    return exp, run, result


async def test_register_finished_run_ac6(container):
    ctx = ctx_for()
    _, _, result = await _register(container, ctx)
    assert result["version"] == 1 and result["stage"] == "none"
    assert container.bus.events_of_type("model_version.created")
    # model card auto fields populated
    card = await container.card_service.get_card(ctx, result["model_id"], 1)
    assert card["algorithm"] == "xgboost"
    assert card["final_metrics"]["f1_score"] == 0.9
    assert card["input_dataset_urns"]


async def test_register_failed_run_rejected_ac6(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="exp-fail")
    run = await container.run_service.create_from_pipeline(
        ctx, {"mlflow_run_id": "run-fail", "experiment_id": exp.id})
    await container.run_service.transition_status(ctx, "pipeline.run.started",
                                                  {"mlflow_run_id": "run-fail"})
    await container.run_service.transition_status(ctx, "pipeline.run.failed",
                                                  {"mlflow_run_id": "run-fail"})
    with pytest.raises(RunNotFinished):
        await container.registry_service.register(ctx, exp.id, run.id,
                                                  {"model_name": "m"})


async def _promote_to(container, ctx, model_id, version, target, approver_sub="reviewer"):
    result = await container.promotion_service.promote(
        ctx, model_id, version, {"target_stage": target})
    approver = ctx_for(tenant_id=ctx.tenant_id, sub=approver_sub)
    return await container.promotion_service.decide(approver, result["promotion_id"], "approve")


async def test_promote_none_staging_production_gate(container):
    ctx = ctx_for()
    _, _, result = await _register(container, ctx)
    mid = result["model_id"]
    d1 = await _promote_to(container, ctx, mid, 1, "staging")
    assert d1["status"] == "approved"
    d2 = await _promote_to(container, ctx, mid, 1, "production")
    assert d2["status"] == "approved"
    v = await container.registry_service.get_version(ctx, mid, 1)
    assert v["stage"] == "production"
    assert container.bus.events_of_type("model_version.promoted")


async def test_four_eyes_self_approval_forbidden_ac8(container):
    ctx = ctx_for(sub="alice")
    _, _, result = await _register(container, ctx)
    promo = await container.promotion_service.promote(
        ctx, result["model_id"], 1, {"target_stage": "staging"})
    with pytest.raises(SelfApprovalForbidden):
        await container.promotion_service.decide(ctx, promo["promotion_id"], "approve")


async def test_pending_conflict_ac8(container):
    ctx = ctx_for()
    _, _, result = await _register(container, ctx)
    await container.promotion_service.promote(ctx, result["model_id"], 1,
                                              {"target_stage": "staging"})
    with pytest.raises(Conflict):
        await container.promotion_service.promote(ctx, result["model_id"], 1,
                                                  {"target_stage": "staging"})


async def test_single_production_auto_archives_incumbent_ac7(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="exp-two")
    r1 = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="r1",
                                 metrics={"f1_score": 0.8})
    r2 = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="r2",
                                 metrics={"f1_score": 0.9})
    reg1 = await container.registry_service.register(ctx, exp.id, r1.id,
                                                     {"model_name": "m"})
    reg2 = await container.registry_service.register(ctx, exp.id, r2.id,
                                                     {"model_name": "m"})
    mid = reg1["model_id"]
    assert reg2["version"] == 2
    await _promote_to(container, ctx, mid, 1, "staging")
    await _promote_to(container, ctx, mid, 1, "production")
    await _promote_to(container, ctx, mid, 2, "staging")
    await _promote_to(container, ctx, mid, 2, "production")
    v1 = await container.registry_service.get_version(ctx, mid, 1)
    v2 = await container.registry_service.get_version(ctx, mid, 2)
    assert v2["stage"] == "production"
    assert v1["stage"] == "archived"  # incumbent auto-archived
    assert container.bus.events_of_type("model_version.archived")


async def test_agent_promotion_records_via_agent_ac9(container):
    obo_ctx = ctx_for(typ="agent_obo", obo_sub="human-1",
                      via_agent={"agent_id": "trainer", "version": "1"})
    _, _, result = await _register(container, obo_ctx)
    promo = await container.promotion_service.promote(
        obo_ctx, result["model_id"], 1, {"target_stage": "staging"})
    history = await container.promotion_service.list_promotions(
        obo_ctx, result["model_id"], 1, 10, None)
    assert history.items[0]["via_agent"]["agent_id"] == "trainer"
    # the OBO user cannot approve their own proposal (four-eyes, AC-9)
    with pytest.raises(SelfApprovalForbidden):
        await container.promotion_service.decide(
            ctx_for(sub="human-1"), promo["promotion_id"], "approve")


async def test_promotion_expiry_ac10(container, clock):
    ctx = ctx_for()
    _, _, result = await _register(container, ctx)
    await container.promotion_service.promote(
        ctx, result["model_id"], 1, {"target_stage": "staging"})
    clock.advance(days=15)
    count = await container.promotion_service.expire_pending_for_tenant(ctx.tenant_id)
    assert count == 1
    history = await container.promotion_service.list_promotions(
        ctx, result["model_id"], 1, 10, None)
    assert history.items[0]["status"] == "expired"
    assert container.bus.events_of_type("model_version.promotion_expired")


async def test_illegal_transition_rejected(container):
    ctx = ctx_for()
    _, _, result = await _register(container, ctx)
    with pytest.raises(ValidationFailed):
        await container.promotion_service.promote(
            ctx, result["model_id"], 1, {"target_stage": "production"})  # none->production

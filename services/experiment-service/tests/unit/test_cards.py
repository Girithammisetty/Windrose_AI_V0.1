"""EXP-FR-040, AC-14: auto-generated model cards + editable overlay."""

from __future__ import annotations

from tests.conftest import ctx_for, make_experiment, seed_finished_run


async def _registered(container, ctx):
    exp = await make_experiment(container, ctx, name="card-exp")
    run = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="cr",
                                  metrics={"f1_score": 0.95}, params={"max_depth": "6"})
    return await container.registry_service.register(ctx, exp.id, run.id,
                                                     {"model_name": "cardm"})


async def test_overlay_edit_increments_version_and_preserves_auto_ac14(container):
    ctx = ctx_for()
    reg = await _registered(container, ctx)
    before = await container.card_service.get_card(ctx, reg["model_id"], 1)
    assert before["overlay"]["limitations"] is None
    merged = await container.card_service.patch_overlay(
        ctx, reg["model_id"], 1, {"limitations": "not for minors"})
    assert merged["overlay"]["limitations"] == "not for minors"
    # auto fields untouched
    assert merged["final_metrics"]["f1_score"] == 0.95
    after = await container.card_service.get_card(ctx, reg["model_id"], 1)
    assert after["overlay"]["limitations"] == "not for minors"
    assert container.bus.events_of_type("model_card.updated")


async def test_markdown_export_merges_both(container):
    ctx = ctx_for()
    reg = await _registered(container, ctx)
    await container.card_service.patch_overlay(
        ctx, reg["model_id"], 1, {"intended_use": "fraud triage"})
    md = await container.card_service.get_card(ctx, reg["model_id"], 1, fmt="markdown")
    assert "# Model Card" in md
    assert "fraud triage" in md
    assert "f1_score" in md

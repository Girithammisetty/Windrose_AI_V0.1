"""EXP-FR-040 / §6: dataset.deleted flags model cards that reference the dataset."""

from __future__ import annotations

from app.events.consumer import DatasetEventHandler
from tests.conftest import ctx_for, make_experiment, seed_finished_run, uid


async def test_dataset_deleted_flags_referencing_cards(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="dd-exp")
    run = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="dd", metrics={"f1": 0.9})
    reg = await container.registry_service.register(ctx, exp.id, run.id, {"model_name": "ddm"})
    dataset_urn = run.input_dataset_urns[0]

    # Before: card is not flagged.
    card = await container.card_service.get_card(ctx, reg["model_id"], 1)
    assert card["training_data_unavailable"] is False

    handler = DatasetEventHandler(container.card_service, container.dedup)
    await handler.handle({
        "event_id": uid(), "event_type": "dataset.deleted", "tenant_id": ctx.tenant_id,
        "resource_urn": dataset_urn, "payload": {}})

    card = await container.card_service.get_card(ctx, reg["model_id"], 1)
    assert card["training_data_unavailable"] is True
    assert container.bus.events_of_type("model_card.updated")


async def test_dataset_deleted_is_idempotent(container):
    ctx = ctx_for()
    exp = await make_experiment(container, ctx, name="dd-exp2")
    run = await seed_finished_run(container, ctx, exp.id, mlflow_run_id="dd2", metrics={"f1": 0.9})
    reg = await container.registry_service.register(ctx, exp.id, run.id, {"model_name": "ddm2"})
    handler = DatasetEventHandler(container.card_service, container.dedup)
    env = {"event_id": uid(), "event_type": "dataset.deleted", "tenant_id": ctx.tenant_id,
           "resource_urn": run.input_dataset_urns[0], "payload": {}}
    await handler.handle(env)
    await handler.handle(env)  # replay -> deduped, no double flag/event
    assert len(container.bus.events_of_type("model_card.updated")) == 1
    assert reg["version"] == 1

"""Learning loop: case.disposition_applied → labeled example → retrain assembly."""

from __future__ import annotations

import json

import pytest

from app.events.envelope import make_envelope
from tests.conftest import TENANT_A

pytestmark = pytest.mark.asyncio

DATASET_URN = "wr:t:dataset:dataset/claims"


def _disposition(row_pk, category, features, *, payload_as_str=False):
    payload = {"dataset_urn": DATASET_URN, "dataset_version": 3, "row_pk": row_pk,
               "disposition": {"id": "d1", "code": "DUP", "category": category},
               "resolution_note": "human triage", "severity": "high",
               "features": features}
    env = make_envelope(event_type="case.disposition_applied", tenant_id=TENANT_A,
                        actor={"type": "user", "id": "analyst"},
                        resource_urn=f"wr:{TENANT_A}:case:case/{row_pk}", payload=payload)
    if payload_as_str:
        env["payload"] = json.dumps(payload)
    return env


async def test_disposition_assembles_labeled_example(container):
    consumer = container.consumer
    await consumer.handle(_disposition("row-1", "fraud",
                                       {"amount": 9999, "prior_claims": 4}))
    async with container.deps.uow_factory(TENANT_A) as uow:
        rows = await uow.labeled_examples.list_for_dataset(DATASET_URN)
    assert len(rows) == 1
    assert rows[0].label == "fraud"
    assert rows[0].features["amount"] == 9999


async def test_disposition_payload_as_json_string_supported(container):
    await container.consumer.handle(
        _disposition("row-2", "legit", {"amount": 5}, payload_as_str=True))
    async with container.deps.uow_factory(TENANT_A) as uow:
        rows = await uow.labeled_examples.list_for_dataset(DATASET_URN)
    assert any(r.row_pk == "row-2" for r in rows)


async def test_dedup_prevents_double_assembly(container):
    env = _disposition("row-3", "fraud", {"amount": 1})
    await container.consumer.handle(env)
    await container.consumer.handle(env)  # same event_id → deduped
    async with container.deps.uow_factory(TENANT_A) as uow:
        rows = await uow.labeled_examples.list_for_dataset(DATASET_URN)
    assert sum(1 for r in rows if r.row_pk == "row-3") == 1


async def test_retrain_run_trains_on_assembled_labels(client, container):
    from tests.conftest import WORKSPACE, auth

    # Feed several corrections.
    for i in range(6):
        cat = "fraud" if i % 2 else "legit"
        await container.consumer.handle(
            _disposition(f"r{i}", cat, {"amount": 100 * i, "prior": i}))
    # Build a training template and run a retrain over the labeled dataset.
    body = {"workspace_id": WORKSPACE, "mode": "train",
            "dataset_refs": {"TRAIN": DATASET_URN}, "name": "retrain",
            "parameters": {"label_column": "label"}}
    tid = (await client.post("/api/v1/algorithm-templates/xgboost/pipelines", json=body,
                             headers=auth())).json()["data"]["id"]
    r = await client.post(f"/api/v1/pipelines/{tid}/run",
                          json={"run_parameters": {"labeled_dataset_urn": DATASET_URN,
                                                   "label_column": "label"}},
                          headers=auth())
    run_id = r.json()["data"]["id"]
    await container.run_service.drive_run(TENANT_A, run_id)
    # The executor received the 6 assembled labeled rows.
    spec = container.deps.executor.specs[-1]
    assert len(spec.rows) == 6
    assert spec.label_column == "label"
    assert {row["label"] for row in spec.rows} == {"fraud", "legit"}

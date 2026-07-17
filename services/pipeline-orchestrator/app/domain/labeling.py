"""Learning-loop labeled-dataset assembly (BRD LEARNING-LOOP TIE-IN).

Consumes ``case.disposition_applied`` events (the human triage correction) and turns
each into a labeled training row: ``dataset_urn + row_pk → features``, and
``disposition.category → label``. The features come from the event's feature snapshot
when present, else are resolved from the dataset feature source. A retrain run then
trains a REAL model on the assembled labeled examples — proving corrections in → a
real model out.
"""

from __future__ import annotations

from app.domain.entities import LabeledExample
from app.utils import new_id


def _parse_payload(env: dict) -> dict:
    """The case envelope carries payload as a JSON string (Avro union); accept a dict
    too (in-process bus)."""
    import json

    payload = env.get("payload")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except Exception:  # noqa: BLE001
            return {}
    return payload or {}


class LabeledExampleAssembler:
    """Idempotent per (dataset_urn, row_pk): re-applying the same correction updates
    the label rather than duplicating the row."""

    def __init__(self, deps, feature_source=None):
        self.d = deps
        self.feature_source = feature_source

    async def handle_disposition(self, env: dict) -> LabeledExample | None:
        tenant_id = env.get("tenant_id", "")
        payload = _parse_payload(env)
        dataset_urn = payload.get("dataset_urn")
        row_pk = str(payload.get("row_pk", "")) if payload.get("row_pk") is not None else ""
        disposition = payload.get("disposition") or {}
        label = disposition.get("category") or disposition.get("code")
        if not (dataset_urn and row_pk and label):
            return None

        features = payload.get("features")
        if not features and self.feature_source is not None:
            features = await self.feature_source.get(tenant_id, dataset_urn, row_pk)
        if not features:
            return None

        async with self.d.uow_factory(tenant_id) as uow:
            example = LabeledExample(
                id=new_id(), tenant_id=tenant_id, dataset_urn=dataset_urn,
                row_pk=row_pk, features=features, label=str(label),
                source_case_urn=env.get("resource_urn"),
                created_at=self.d.clock.now())
            await uow.labeled_examples.upsert(example)
        return example

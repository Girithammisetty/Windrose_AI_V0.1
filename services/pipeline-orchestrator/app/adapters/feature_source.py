"""Feature resolution for the learning loop: given (dataset_urn, row_pk) return the
row's feature vector. Used when a ``case.disposition_applied`` event does not embed a
feature snapshot. The real adapter reads the dataset materialized as CSV in object
storage (MinIO); the in-memory one is unit-tier only."""

from __future__ import annotations

import io
import re


def _key(dataset_urn: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", dataset_urn).strip("_")
    return f"features/{slug}.csv"


class InMemoryFeatureSource:
    def __init__(self):
        self._rows: dict[tuple[str, str], dict] = {}

    def put(self, dataset_urn: str, row_pk: str, features: dict) -> None:
        self._rows[(dataset_urn, str(row_pk))] = features

    async def get(self, tenant_id: str, dataset_urn: str, row_pk: str):
        return self._rows.get((dataset_urn, str(row_pk)))


class ObjectStoreFeatureSource:
    """Reads a dataset's feature CSV from MinIO once and indexes rows by ``row_pk``
    (the CSV's first column or a ``row_pk`` column)."""

    def __init__(self, object_store, *, pk_column: str = "row_pk"):
        self._store = object_store
        self._pk = pk_column
        self._cache: dict[str, dict[str, dict]] = {}

    async def _load(self, dataset_urn: str) -> dict[str, dict]:
        # Only NON-empty results are cached. A miss (CSV absent or empty) is never
        # cached, so a lookup that happens before the feature data lands re-reads and
        # succeeds once the data appears (BUG-3: no negative-cache poisoning).
        cached = self._cache.get(dataset_urn)
        if cached:
            return cached
        import pandas as pd

        try:
            raw = await self._store.get(_key(dataset_urn))
        except Exception:  # noqa: BLE001 — dataset not materialized as features CSV yet
            return {}
        df = pd.read_csv(io.BytesIO(raw))
        pk = self._pk if self._pk in df.columns else df.columns[0]
        index: dict[str, dict] = {}
        for _, row in df.iterrows():
            rec = row.to_dict()
            index[str(rec[pk])] = {k: v for k, v in rec.items() if k != pk}
        if index:
            self._cache[dataset_urn] = index
        return index

    async def get(self, tenant_id: str, dataset_urn: str, row_pk: str):
        index = await self._load(dataset_urn)
        return index.get(str(row_pk))

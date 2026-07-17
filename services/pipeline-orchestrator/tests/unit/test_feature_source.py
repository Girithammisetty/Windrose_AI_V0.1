"""BUG-3: ObjectStoreFeatureSource must not negative-cache. A lookup that misses
before the feature CSV lands must succeed once the data appears."""

from __future__ import annotations

import pytest

from app.adapters.feature_source import ObjectStoreFeatureSource, _key

pytestmark = pytest.mark.asyncio

URN = "wr:t:dataset:dataset/claims"


class _Store:
    def __init__(self):
        self.data: dict[str, bytes] = {}
        self.reads = 0

    async def get(self, key: str) -> bytes:
        self.reads += 1
        if key not in self.data:
            raise KeyError(key)
        return self.data[key]


async def test_miss_then_data_appears_then_success():
    store = _Store()
    src = ObjectStoreFeatureSource(store)

    # First lookup misses (CSV not materialized yet) and must NOT be cached.
    assert await src.get("t", URN, "r1") is None

    # Feature data lands.
    store.data[_key(URN)] = b"row_pk,amount,prior\nr1,100,2\nr2,9000,5\n"

    got = await src.get("t", URN, "r1")
    assert got is not None
    assert got["amount"] == 100 and got["prior"] == 2
    # A later hit is served from cache (no re-read once data is present).
    reads_before = store.reads
    again = await src.get("t", URN, "r2")
    assert again["amount"] == 9000
    assert store.reads == reads_before  # cached, not re-read

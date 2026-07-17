"""Unit: version retention policy (DST-FR-080/081, AC-6) + soft-delete purge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from app.domain.entities import DatasetVersion, LineageEdge
from app.domain.retention import RetentionPolicy, select_expirable
from app.domain.services import CallCtx
from app.domain.urn import version_urn
from tests.conftest import TENANT_A, auth, create_dataset

NOW = datetime(2026, 7, 9, tzinfo=UTC)
CTX = CallCtx(tenant_id=TENANT_A, actor={"type": "service", "id": "retention-job"})


def version(no: int, *, days_old: int, vid: str | None = None) -> DatasetVersion:
    return DatasetVersion(
        id=vid or f"v{no}", tenant_id=TENANT_A, dataset_id="d1", version_no=no,
        iceberg_snapshot_id=1000 + no, schema={},
        created_at=NOW - timedelta(days=days_old),
    )


class TestSelectExpirable:
    def test_recent_versions_kept(self):
        versions = [version(i, days_old=10 * i) for i in range(1, 5)]  # all < 90d
        assert select_expirable(
            versions, now=NOW, policy=RetentionPolicy(),
            current_version_id="v4", pinned_version_ids=set(),
        ) == []

    def test_keep_last_n_beyond_window(self):
        versions = [version(i, days_old=400 - i) for i in range(1, 16)]  # all old
        policy = RetentionPolicy(keep_last=10, monthly_months=0)
        expirable = select_expirable(
            versions, now=NOW, policy=policy,
            current_version_id="v15", pinned_version_ids=set(),
        )
        assert sorted(v.version_no for v in expirable) == [1, 2, 3, 4, 5]

    def test_monthly_boundaries_kept(self):
        # two versions per month across 6 months, all older than 90d
        versions, no = [], 1
        for month_back in range(4, 10):
            for day in (1, 15):
                versions.append(version(no, days_old=month_back * 31 - day))
                no += 1
        policy = RetentionPolicy(keep_last=0, monthly_months=13)
        expirable = select_expirable(
            versions, now=NOW, policy=policy,
            current_version_id=None, pinned_version_ids=set(),
        )
        # exactly one boundary version per calendar month survives
        survivors = {v.version_no for v in versions} - {v.version_no for v in expirable}
        assert len(survivors) == len({
            (v.created_at.year, v.created_at.month) for v in versions
        })

    def test_current_and_pinned_never_expire(self):
        versions = [version(i, days_old=500) for i in range(1, 6)]
        policy = RetentionPolicy(keep_last=0, monthly_months=0)
        expirable = select_expirable(
            versions, now=NOW, policy=policy,
            current_version_id="v5", pinned_version_ids={"v2"},
        )
        assert sorted(v.version_no for v in expirable) == [1, 3, 4]


class TestRetentionJob:
    async def test_ac6_retention_run(self, client, container, clock):
        """AC-6: v1..v12 older than policy -> expired (snapshots expired, profile
        objects deleted) except current v12 and trained-pinned v3 (<400d)."""
        c = container
        state = c.memory_state
        ds = await create_dataset(client, name="Aged")
        dataset = state.datasets[ds["id"]]

        now = clock.now()
        for no in range(1, 13):
            await c.catalog.commit_snapshot(
                dataset.iceberg_table, 1000 + no, pd.DataFrame({"a": [no]})
            )
            v = DatasetVersion(
                id=f"v{no}", tenant_id=TENANT_A, dataset_id=dataset.id, version_no=no,
                iceberg_snapshot_id=1000 + no, schema={},
                created_at=now - timedelta(days=200 - no),
            )
            state.versions[v.id] = v
        dataset.current_version_id = "v12"
        dataset.status = "ready"

        # trained pin on v3, edge 100 days old (< 400d)
        pin = LineageEdge(
            id="pin", tenant_id=TENANT_A,
            from_urn=version_urn(TENANT_A, dataset.id, 3),
            to_urn=f"wr:{TENANT_A}:experiment:model/m1",
            activity="trained",
            occurred_at=now - timedelta(days=100), created_at=now,
        )
        state.edges[pin.id] = pin

        policy = RetentionPolicy(keep_all_days=90, keep_last=1, monthly_months=0,
                                 trained_pin_days=400)
        result = await c.retention_service.run_for_tenant(CTX, policy)

        survivors = {v.version_no for v in state.versions.values() if not v.expired}
        expired = {v.version_no for v in state.versions.values() if v.expired}
        assert survivors == {3, 12}
        assert expired == {1, 2, 4, 5, 6, 7, 8, 9, 10, 11}
        assert result["expired_versions"] == 10
        # snapshots expired in the catalog, rows retained with expired=true
        assert not await c.catalog.snapshot_exists(dataset.iceberg_table, 1001)
        assert await c.catalog.snapshot_exists(dataset.iceberg_table, 1003)
        assert await c.catalog.snapshot_exists(dataset.iceberg_table, 1012)
        assert len(state.events_of_type("dataset.version_expired")) == 10

    async def test_purges_soft_deleted_after_window_lineage_survives(
        self, client, container, clock
    ):
        """BR-8: hard cleanup drops rows + Iceberg table; lineage edges survive."""
        c = container
        state = c.memory_state
        ds = await create_dataset(client, name="Purge")
        dataset = state.datasets[ds["id"]]
        await c.catalog.commit_snapshot(dataset.iceberg_table, 1, pd.DataFrame({"a": [1]}))
        edge = LineageEdge(
            id="e1", tenant_id=TENANT_A,
            from_urn=f"wr:{TENANT_A}:ingestion:ingestion/i1",
            to_urn=ds["urn"], activity="ingested",
            occurred_at=clock.now(), created_at=clock.now(),
        )
        state.edges[edge.id] = edge

        await client.delete(f"/api/v1/datasets/{ds['id']}?force=true", headers=auth())
        clock.advance(days=31)
        result = await c.retention_service.run_for_tenant(CTX)
        assert result["purged_datasets"] == 1
        assert ds["id"] not in state.datasets
        assert not await c.catalog.snapshot_exists(dataset.iceberg_table, 1)
        assert "e1" in state.edges  # lineage is a historical record

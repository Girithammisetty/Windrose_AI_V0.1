"""Real Iceberg REST catalog + MinIO integration: two-phase stage/commit append,
snapshot-id advance, BR-9 has_snapshot, read-back and drop."""

from __future__ import annotations

import pytest

from windrose_common.iceberg import IcebergRestCatalog, IcebergTableWriter, RowBatch

pytestmark = pytest.mark.integration


async def _batches(rows_per_batch, n_batches, cols):
    for b in range(n_batches):
        yield RowBatch(
            columns=cols,
            rows=[[f"v{b}_{i}_{c}" for c in range(len(cols))] for i in range(rows_per_batch)],
        )


async def test_stage_commit_snapshot_advances_and_reads_back(iceberg, unique):
    table = f"bronze.pyc{unique}.ds_orders"
    writer = IcebergTableWriter()
    catalog = IcebergRestCatalog()
    cols = ["id", "name"]

    try:
        # first append
        staged = await writer.stage(
            table, _batches(10, 2, cols), {"ingestion_id": "ing-1", "source": "upload"}
        )
        assert staged.rows == 20
        result1 = await writer.commit(staged)
        assert result1.rows_appended == 20
        assert result1.snapshot_id > 0

        # BR-9: the ingestion_id is now present as a snapshot summary property
        assert await writer.has_snapshot(table, "ing-1") is True
        assert await writer.has_snapshot(table, "ing-does-not-exist") is False

        # snapshot exists via the read-side catalog port + reads back the rows
        assert await catalog.snapshot_exists(table, result1.snapshot_id) is True
        df1 = await catalog.read_snapshot(table, result1.snapshot_id)
        assert len(df1) == 20
        assert set(df1.columns) == {"id", "name"}

        # second append -> a NEW snapshot id (advances)
        staged2 = await writer.stage(
            table, _batches(5, 1, cols), {"ingestion_id": "ing-2", "source": "upload"}
        )
        result2 = await writer.commit(staged2)
        assert result2.snapshot_id != result1.snapshot_id
        df2 = await catalog.read_snapshot(table, result2.snapshot_id)
        assert len(df2) == 25  # cumulative append
    finally:
        await catalog.drop_table(table)

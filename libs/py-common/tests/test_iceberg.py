"""Real Iceberg REST catalog + MinIO integration: two-phase stage/commit append,
snapshot-id advance, BR-9 has_snapshot, read-back and drop."""

from __future__ import annotations

import os
import tracemalloc

import pytest

from datacern_common.iceberg import (
    IcebergRestCatalog,
    IcebergTableWriter,
    RowBatch,
    _split_identifier,
)

pytestmark = pytest.mark.integration


async def _batches(rows_per_batch, n_batches, cols):
    for b in range(n_batches):
        yield RowBatch(
            columns=cols,
            rows=[[f"v{b}_{i}_{c}" for c in range(len(cols))] for i in range(rows_per_batch)],
        )


async def _no_batches():
    return
    yield  # pragma: no cover - makes this an async generator


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


async def test_commit_streams_large_file_in_bounded_chunks(iceberg, unique):
    """B1: commit() must stream the staged file to Iceberg in bounded row
    chunks instead of materializing the whole file in memory. A tiny
    commit_chunk_rows forces multiple chunks for one commit -- proven here by
    counting the resulting Iceberg snapshots directly (one per chunk), not by
    inference: 12 rows / chunk_rows=3 must yield exactly 4 new snapshots."""
    table = f"bronze.pyc{unique}.ds_big"
    writer = IcebergTableWriter(commit_chunk_rows=3)
    catalog = IcebergRestCatalog()
    cols = ["id", "name"]

    try:
        staged = await writer.stage(
            table, _batches(4, 3, cols), {"ingestion_id": "ing-big", "source": "upload"}
        )
        assert staged.rows == 12
        result = await writer.commit(staged)
        assert result.rows_appended == 12

        # correctness survives chunking: all rows present under the final snapshot
        df = await catalog.read_snapshot(table, result.snapshot_id)
        assert len(df) == 12

        # BR-9 guard still satisfied even though ingestion_id is stamped on
        # every chunk's snapshot, not just one
        assert await writer.has_snapshot(table, "ing-big") is True

        # genuine streaming, not a fake pass-through: exactly 4 snapshots were
        # created for this single commit (12 rows / chunk_rows=3)
        ns, name = _split_identifier(table)
        tbl = writer._cat().load_table((ns, name))
        assert len(list(tbl.snapshots())) == 4
    finally:
        await catalog.drop_table(table)


async def test_commit_empty_ingestion_still_creates_ingestion_id_marker(iceberg, unique):
    """A 0-row staged file must still create exactly one snapshot carrying
    ingestion_id, so has_snapshot()'s BR-9 double-append guard recognises the
    (empty) ingestion as already processed on retry."""
    table = f"bronze.pyc{unique}.ds_empty"
    writer = IcebergTableWriter(commit_chunk_rows=3)
    catalog = IcebergRestCatalog()
    cols = ["id", "name"]

    try:
        staged = await writer.stage(
            table, _no_batches(), {"ingestion_id": "ing-empty", "source": "upload", "columns": cols}
        )
        assert staged.rows == 0
        result = await writer.commit(staged)
        assert result.rows_appended == 0
        assert await writer.has_snapshot(table, "ing-empty") is True

        ns, name = _split_identifier(table)
        tbl = writer._cat().load_table((ns, name))
        assert len(list(tbl.snapshots())) == 1
    finally:
        await catalog.drop_table(table)


def _volume_rows() -> int:
    """WS5 (BRD 58): "a volume load test at 1M rows for WS4 items" (B1).
    Defaults to a size that completes in a reasonable soak window; override
    with ICEBERG_VOLUME_ROWS=1000000 for the BRD's literal scale."""
    try:
        return int(os.environ.get("ICEBERG_VOLUME_ROWS", "100000"))
    except ValueError:
        return 100_000


async def test_commit_streams_large_volume_in_bounded_memory(iceberg, unique):
    """B1 volume/load test: commit() must stay bounded in memory at real scale,
    not just the tiny fixtures the other tests use. Measures PEAK memory
    (tracemalloc) during commit() itself and asserts it stays a small fraction
    of what materializing the whole staged file at once would need -- proving
    the streaming fix (BRD58 B1) holds at volume, not just in principle."""
    n = _volume_rows()
    rows_per_batch = 10_000
    n_batches = max(1, n // rows_per_batch)
    table = f"bronze.pyc{unique}.ds_volume"
    writer = IcebergTableWriter()  # real default commit_chunk_rows (50_000)
    catalog = IcebergRestCatalog()
    cols = ["id", "name", "description"]

    try:
        staged = await writer.stage(
            table, _batches(rows_per_batch, n_batches, cols),
            {"ingestion_id": "ing-volume", "source": "upload"},
        )
        assert staged.rows == rows_per_batch * n_batches

        tracemalloc.start()
        result = await writer.commit(staged)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert result.rows_appended == staged.rows

        # Each row is ~3 short string fields; a full-file in-memory
        # materialization (the pre-fix behavior, 3 copies per BRD58's B1 log)
        # would scale linearly with row count and run into the hundreds of MB
        # at this volume. Streaming in commit_chunk_rows-sized chunks bounds
        # peak allocation to roughly one chunk's worth regardless of row
        # count -- assert peak stays under a fixed ceiling, not a per-row one.
        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 200, (
            f"commit() peak memory {peak_mb:.1f}MB at {staged.rows} rows -- "
            "expected it to stay bounded by commit_chunk_rows, not grow with row count"
        )

        df = await catalog.read_snapshot(table, result.snapshot_id)
        assert len(df) == staged.rows
    finally:
        await catalog.drop_table(table)

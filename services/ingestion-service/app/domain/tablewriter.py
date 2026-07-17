"""TableWriter port — Iceberg bronze append (ING-FR-043, BR-9).

Two-phase API so the state machine can transition running -> committing between
decode/write and the single atomic snapshot commit:

    staged = await writer.stage(table, batches, summary)   # streaming write
    result = await writer.commit(staged)                    # exactly-one snapshot

ParquetFileTableWriter is the unit-tier double (real parquet files + a snapshot
ledger). IcebergTableWriter is the real runtime writer (pyiceberg REST catalog +
MinIO via windrose_common).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from app.ids import uuid7


@dataclass(slots=True)
class RowBatch:
    columns: list[str]
    rows: list[list[Any]]


@dataclass(slots=True)
class StagedAppend:
    table: str
    rows: int
    bytes_written: int
    summary: dict[str, Any]
    staging_token: str
    columns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AppendResult:
    snapshot_id: int
    rows_appended: int
    bytes_written: int


class TableWriter(Protocol):
    async def stage(
        self, table: str, batches: AsyncIterator[RowBatch], summary: dict[str, Any]
    ) -> StagedAppend: ...

    async def commit(self, staged: StagedAppend) -> AppendResult: ...

    async def discard(self, staged: StagedAppend) -> None: ...

    async def has_snapshot(self, table: str, ingestion_id: str) -> bool:
        """BR-9: verify snapshot presence by ingestion_id to avoid double-append."""
        ...


class ParquetFileTableWriter:
    """Fake Iceberg: parquet data files + snapshots.json ledger per table.

    The ledger append is the 'commit' — atomic (write temp + rename), exactly
    one entry per job, carrying the snapshot summary metadata {ingestion_id,
    source} like an Iceberg snapshot would (BR-9).
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _table_dir(self, table: str) -> Path:
        safe = table.replace("/", "_")
        path = self.root / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ledger_path(self, table: str) -> Path:
        return self._table_dir(table) / "snapshots.json"

    def _read_ledger(self, table: str) -> list[dict[str, Any]]:
        path = self._ledger_path(table)
        if not path.is_file():
            return []
        return json.loads(path.read_text())

    async def stage(
        self, table: str, batches: AsyncIterator[RowBatch], summary: dict[str, Any]
    ) -> StagedAppend:
        token = uuid7()
        staging = self._table_dir(table) / f".staging-{token}.parquet"
        writer: pq.ParquetWriter | None = None
        schema: pa.Schema | None = None
        columns: list[str] = []
        rows = 0
        try:
            async for batch in batches:
                if writer is None:
                    columns = list(batch.columns)
                    schema = pa.schema([pa.field(c, pa.string()) for c in columns])
                    writer = pq.ParquetWriter(staging, schema)
                arrays = []
                for idx, _col in enumerate(columns):
                    values = [
                        str(row[idx]) if idx < len(row) and row[idx] is not None else None
                        for row in batch.rows
                    ]
                    arrays.append(pa.array(values, type=pa.string()))
                writer.write_batch(pa.record_batch(arrays, schema=schema))
                rows += len(batch.rows)
        except BaseException:
            if writer is not None:
                writer.close()
            staging.unlink(missing_ok=True)
            raise
        if writer is not None:
            writer.close()
        else:
            # zero rows: stage an empty file with an empty schema (allow_empty path)
            pq.ParquetWriter(staging, pa.schema([])).close()
        return StagedAppend(
            table=table,
            rows=rows,
            bytes_written=staging.stat().st_size,
            summary=dict(summary),
            staging_token=token,
            columns=columns,
        )

    async def commit(self, staged: StagedAppend) -> AppendResult:
        table_dir = self._table_dir(staged.table)
        staging = table_dir / f".staging-{staged.staging_token}.parquet"
        ledger = self._read_ledger(staged.table)
        snapshot_id = (max((s["snapshot_id"] for s in ledger), default=0)) + 1
        data_file = table_dir / f"snap-{snapshot_id:08d}.parquet"
        os.replace(staging, data_file)
        ledger.append(
            {
                "snapshot_id": snapshot_id,
                "rows": staged.rows,
                "bytes": staged.bytes_written,
                "columns": staged.columns,
                "summary": staged.summary,
                "committed_at": datetime.now(UTC).isoformat(),
                "data_file": data_file.name,
            }
        )
        tmp = self._ledger_path(staged.table).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ledger, indent=1))
        os.replace(tmp, self._ledger_path(staged.table))
        return AppendResult(
            snapshot_id=snapshot_id, rows_appended=staged.rows, bytes_written=staged.bytes_written
        )

    async def discard(self, staged: StagedAppend) -> None:
        staging = self._table_dir(staged.table) / f".staging-{staged.staging_token}.parquet"
        staging.unlink(missing_ok=True)

    async def has_snapshot(self, table: str, ingestion_id: str) -> bool:
        return any(
            s["summary"].get("ingestion_id") == ingestion_id for s in self._read_ledger(table)
        )

    # test helpers ---------------------------------------------------------
    def snapshots(self, table: str) -> list[dict[str, Any]]:
        return self._read_ledger(table)

    def all_snapshots(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.root.is_dir():
            for table_dir in self.root.iterdir():
                if (table_dir / "snapshots.json").is_file():
                    out.extend(json.loads((table_dir / "snapshots.json").read_text()))
        return out


class IcebergTableWriter:
    """Real Iceberg bronze writer via the shared ``windrose_common`` pyiceberg
    adapter against the REST catalog + MinIO. ``stage`` streams batches into a
    temporary parquet file (bounded); ``commit`` appends it to
    ``bronze.<tenant_id>.ds_<dataset_id>`` in exactly one snapshot carrying the
    ``ingestion_id`` summary (BR-9). Runtime table writer."""

    def __init__(
        self,
        catalog_uri: str = "http://localhost:8181",
        *,
        warehouse: str = "s3://windrose-warehouse/",
        s3_endpoint: str = "http://localhost:9000",
        s3_access_key: str = "windrose",
        s3_secret_key: str = "windrose_dev",
        s3_region: str = "us-east-1",
    ) -> None:
        from windrose_common.iceberg import IcebergConfig
        from windrose_common.iceberg import IcebergTableWriter as _Writer

        cfg = IcebergConfig(
            uri=catalog_uri,
            warehouse=warehouse,
            s3_endpoint=s3_endpoint,
            s3_access_key=s3_access_key,
            s3_secret_key=s3_secret_key,
            s3_region=s3_region,
        )
        self._writer = _Writer(cfg)

    async def stage(
        self, table: str, batches: AsyncIterator[RowBatch], summary: dict[str, Any]
    ) -> StagedAppend:
        staged = await self._writer.stage(table, batches, summary)
        return StagedAppend(
            table=staged.table,
            rows=staged.rows,
            bytes_written=staged.bytes_written,
            summary=staged.summary,
            staging_token=staged.staging_token,
            columns=staged.columns,
        )

    async def commit(self, staged: StagedAppend) -> AppendResult:
        result = await self._writer.commit(self._to_common(staged))
        return AppendResult(
            snapshot_id=result.snapshot_id,
            rows_appended=result.rows_appended,
            bytes_written=result.bytes_written,
        )

    async def discard(self, staged: StagedAppend) -> None:
        await self._writer.discard(self._to_common(staged))

    async def has_snapshot(self, table: str, ingestion_id: str) -> bool:
        return await self._writer.has_snapshot(table, ingestion_id)

    def _to_common(self, staged: StagedAppend):
        from windrose_common.iceberg import StagedAppend as _Staged

        staging = Path(tempfile.gettempdir()) / f"wr-iceberg-stage-{staged.staging_token}.parquet"
        return _Staged(
            table=staged.table,
            rows=staged.rows,
            bytes_written=staged.bytes_written,
            summary=staged.summary,
            staging_token=staged.staging_token,
            columns=staged.columns,
            path=str(staging),
        )

"""Real Iceberg lakehouse adapters using pyiceberg against the REST catalog.

Two ports are served (both Datacern services model the lakehouse differently):

* ``IcebergTableWriter`` — ingestion-service's two-phase ``TableWriter``:
  ``stage`` streams row batches into a temporary parquet file (bounded — the
  parquet writer flushes row groups; nothing accumulates the whole file in
  memory), and ``commit`` appends that file to the Iceberg table in exactly one
  snapshot whose summary carries ``ingestion_id`` (BR-9 double-append guard).
* ``IcebergRestCatalog`` — dataset-service's read-side ``Catalog``:
  ``snapshot_exists`` / ``read_snapshot`` (time-travel to a snapshot id) /
  ``expire_snapshot`` / ``drop_table``.

Table identifiers are the dotted strings the services already use
(``bronze.<tenant>.ds_<dataset>``): the last segment is the table name, the rest
is the (multi-level) namespace, created on demand.

Bronze is a string-typed landing layer, matching the ingestion decoder which
emits every column as a string; the schema is created from the first commit's
columns.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


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
    path: str | None = None


@dataclass(slots=True)
class AppendResult:
    snapshot_id: int
    rows_appended: int
    bytes_written: int


@dataclass(slots=True)
class IcebergConfig:
    uri: str = "http://localhost:8181"
    warehouse: str = "s3://datacern-warehouse/"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "datacern"
    s3_secret_key: str = "datacern_dev"
    s3_region: str = "us-east-1"

    def rest_properties(self) -> dict[str, str]:
        return {
            "uri": self.uri,
            "warehouse": self.warehouse,
            "s3.endpoint": self.s3_endpoint,
            "s3.access-key-id": self.s3_access_key,
            "s3.secret-access-key": self.s3_secret_key,
            "s3.path-style-access": "true",
            "s3.region": self.s3_region,
        }


def load_rest_catalog(cfg: IcebergConfig, name: str = "datacern"):
    from pyiceberg.catalog.rest import RestCatalog

    return RestCatalog(name, **cfg.rest_properties())


def _split_identifier(table: str) -> tuple[str, str]:
    parts = table.split(".")
    namespace = ".".join(parts[:-1]) or "default"
    return namespace, parts[-1]


def _string_schema(columns: list[str]):
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    return Schema(
        *[
            NestedField(field_id=i + 1, name=col, field_type=StringType(), required=False)
            for i, col in enumerate(columns)
        ]
    )


def _iter_string_chunks(columns: list[str], path: str, chunk_rows: int) -> Iterator[pa.Table]:
    """Stream the staged parquet file back in bounded row chunks, each already
    cast to the all-string schema for append -- commit must never hold the
    whole staged file in memory at once (B1: a large ingestion was OOMing at
    commit even though stage() itself streams and is bounded)."""
    string_schema = pa.schema([pa.field(c, pa.large_string()) for c in columns])
    for batch in pq.ParquetFile(path).iter_batches(batch_size=chunk_rows):
        yield pa.Table.from_batches([batch]).cast(string_schema)


# The pyiceberg RestCatalog wraps a single requests.Session that is NOT
# thread-safe; `to_thread` fans concurrent catalog ops (e.g. a dashboard batch
# resolving several charts at once, each auto-materializing a dataset) onto the
# shared session, which intermittently corrupts responses -> ServerError/500.
# Serialize all catalog operations on one process-wide lock. Ops are fast
# (metadata only), so the throughput cost is negligible for this workload.
_CATALOG_LOCK = threading.Lock()


class _CatalogHolder:
    def __init__(self, cfg: IcebergConfig | None = None, catalog=None) -> None:
        self.cfg = cfg or IcebergConfig()
        self._catalog = catalog

    def _cat(self):
        if self._catalog is None:
            self._catalog = load_rest_catalog(self.cfg)
        return self._catalog


class IcebergTableWriter(_CatalogHolder):
    """ingestion-service TableWriter port backed by a real Iceberg REST catalog."""

    def __init__(
        self,
        cfg: IcebergConfig | None = None,
        catalog=None,
        commit_chunk_rows: int = 50_000,
    ) -> None:
        super().__init__(cfg, catalog)
        # Bounds peak memory at commit time to ~one chunk's worth of rows
        # regardless of staged file size (B1). Each chunk becomes its own
        # Iceberg append/snapshot; has_snapshot()'s any-snapshot check and the
        # BR-9 double-append guard are unaffected since every chunk of a given
        # commit carries the same ingestion_id.
        self.commit_chunk_rows = commit_chunk_rows

    async def stage(
        self, table: str, batches: AsyncIterator[RowBatch], summary: dict[str, Any]
    ) -> StagedAppend:
        token = uuid.uuid4().hex
        staging_path = Path(tempfile.gettempdir()) / f"wr-iceberg-stage-{token}.parquet"
        writer: pq.ParquetWriter | None = None
        schema: pa.Schema | None = None
        columns: list[str] = []
        rows = 0
        try:
            async for batch in batches:
                if writer is None:
                    columns = list(batch.columns)
                    schema = pa.schema([pa.field(c, pa.large_string()) for c in columns])
                    writer = pq.ParquetWriter(str(staging_path), schema)
                arrays = []
                for idx in range(len(columns)):
                    values = [
                        str(row[idx]) if idx < len(row) and row[idx] is not None else None
                        for row in batch.rows
                    ]
                    arrays.append(pa.array(values, type=pa.large_string()))
                writer.write_batch(pa.record_batch(arrays, schema=schema))
                rows += len(batch.rows)
        except BaseException:
            if writer is not None:
                writer.close()
            staging_path.unlink(missing_ok=True)
            raise
        if writer is not None:
            writer.close()
        else:
            columns = list(summary.get("columns", []))
            schema = pa.schema([pa.field(c, pa.large_string()) for c in columns])
            pq.ParquetWriter(str(staging_path), schema).close()
        return StagedAppend(
            table=table,
            rows=rows,
            bytes_written=staging_path.stat().st_size,
            summary=dict(summary),
            staging_token=token,
            columns=columns,
            path=str(staging_path),
        )

    async def commit(self, staged: StagedAppend) -> AppendResult:
        return await asyncio.to_thread(self._commit_sync, staged)

    def _commit_sync(self, staged: StagedAppend) -> AppendResult:
        from pyiceberg.exceptions import NoSuchTableError

        catalog = self._cat()
        namespace, name = _split_identifier(staged.table)
        try:
            catalog.create_namespace(namespace)
        except Exception:  # noqa: BLE001 — already exists
            pass
        schema = _string_schema(staged.columns)
        try:
            tbl = catalog.load_table((namespace, name))
        except NoSuchTableError:
            tbl = catalog.create_table((namespace, name), schema=schema)

        # cast to the table's on-disk arrow schema so field ids line up
        target_schema = tbl.schema().as_arrow()
        snapshot_props = {
            "ingestion_id": str(staged.summary.get("ingestion_id", "")),
            "source": str(staged.summary.get("source", "")),
        }
        appended = False
        for chunk in _iter_string_chunks(staged.columns, staged.path, self.commit_chunk_rows):
            tbl.append(chunk.cast(target_schema), snapshot_properties=snapshot_props)
            appended = True
        if not appended:
            # 0-row ingestion: still create exactly one snapshot carrying
            # ingestion_id so has_snapshot() recognises this ingestion as done.
            string_schema = pa.schema(
                [pa.field(c, pa.large_string()) for c in staged.columns]
            )
            tbl.append(string_schema.empty_table().cast(target_schema),
                       snapshot_properties=snapshot_props)
        tbl.refresh()
        snap = tbl.current_snapshot()
        Path(staged.path).unlink(missing_ok=True)
        return AppendResult(
            snapshot_id=snap.snapshot_id,
            rows_appended=staged.rows,
            bytes_written=staged.bytes_written,
        )

    async def discard(self, staged: StagedAppend) -> None:
        if staged.path:
            Path(staged.path).unlink(missing_ok=True)

    async def has_snapshot(self, table: str, ingestion_id: str) -> bool:
        return await asyncio.to_thread(self._has_snapshot_sync, table, ingestion_id)

    def _has_snapshot_sync(self, table: str, ingestion_id: str) -> bool:
        from pyiceberg.exceptions import NoSuchTableError

        catalog = self._cat()
        namespace, name = _split_identifier(table)
        try:
            tbl = catalog.load_table((namespace, name))
        except NoSuchTableError:
            return False
        for snap in tbl.snapshots():
            if (snap.summary and snap.summary.get("ingestion_id") == ingestion_id):
                return True
        return False


class IcebergRestCatalog(_CatalogHolder):
    """dataset-service read-side Catalog port backed by a real Iceberg REST catalog."""

    async def snapshot_exists(self, table: str, snapshot_id: int) -> bool:
        return await asyncio.to_thread(self._snapshot_exists_sync, table, snapshot_id)

    def _snapshot_exists_sync(self, table: str, snapshot_id: int) -> bool:
        from pyiceberg.exceptions import NoSuchTableError

        with _CATALOG_LOCK:
            catalog = self._cat()
            namespace, name = _split_identifier(table)
            try:
                tbl = catalog.load_table((namespace, name))
            except NoSuchTableError:
                return False
            return any(s.snapshot_id == snapshot_id for s in tbl.snapshots())

    async def read_snapshot(self, table: str, snapshot_id: int):
        return await asyncio.to_thread(self._read_snapshot_sync, table, snapshot_id)

    def _read_snapshot_sync(self, table: str, snapshot_id: int):
        with _CATALOG_LOCK:
            catalog = self._cat()
            namespace, name = _split_identifier(table)
            tbl = catalog.load_table((namespace, name))
            return tbl.scan(snapshot_id=snapshot_id).to_pandas()

    async def read_snapshot_head(self, table: str, snapshot_id: int, max_rows: int):
        """Read at most ``max_rows`` rows of a snapshot, pushing the row limit
        into the Iceberg scan so memory stays bounded regardless of table size
        (paged browse/read paths must never materialize a whole warehouse-scale
        snapshot into one process heap)."""
        return await asyncio.to_thread(
            self._read_snapshot_head_sync, table, snapshot_id, max_rows
        )

    def _read_snapshot_head_sync(self, table: str, snapshot_id: int, max_rows: int):
        with _CATALOG_LOCK:
            catalog = self._cat()
            namespace, name = _split_identifier(table)
            tbl = catalog.load_table((namespace, name))
            return tbl.scan(snapshot_id=snapshot_id, limit=max(0, max_rows)).to_pandas()

    async def data_file_uris(self, table: str, snapshot_id: int | None = None) -> list[str]:
        """Return the exact ``s3://...`` parquet data files backing a snapshot.

        Enumerated authoritatively via the Iceberg manifest (``plan_files``) —
        NOT by globbing a prefix — so only the files pinned by ``snapshot_id``
        are returned. A ``None`` snapshot_id resolves to the table's current
        snapshot. These are the physical files query-service reads to run SQL
        directly over the claims data (QRY-FR-005)."""
        return await asyncio.to_thread(self._data_file_uris_sync, table, snapshot_id)

    def _data_file_uris_sync(self, table: str, snapshot_id: int | None) -> list[str]:
        with _CATALOG_LOCK:
            catalog = self._cat()
            namespace, name = _split_identifier(table)
            tbl = catalog.load_table((namespace, name))
            if snapshot_id is None:
                snap = tbl.current_snapshot()
                if snap is None:
                    return []
                snapshot_id = snap.snapshot_id
            scan = tbl.scan(snapshot_id=snapshot_id)
            return [task.file.file_path for task in scan.plan_files()]

    async def table_columns(self, table: str) -> list[dict[str, str]]:
        """Return the table's columns as ``[{"name", "type"}, ...]`` from the
        Iceberg schema — the source of truth when a DatasetVersion carries an
        empty schema map (bronze is created string-typed from the ingest
        columns)."""
        return await asyncio.to_thread(self._table_columns_sync, table)

    def _table_columns_sync(self, table: str) -> list[dict[str, str]]:
        with _CATALOG_LOCK:
            catalog = self._cat()
            namespace, name = _split_identifier(table)
            tbl = catalog.load_table((namespace, name))
            return [
                {"name": f.name, "type": str(f.field_type)} for f in tbl.schema().fields
            ]

    async def expire_snapshot(self, table: str, snapshot_id: int) -> None:
        await asyncio.to_thread(self._expire_snapshot_sync, table, snapshot_id)

    def _expire_snapshot_sync(self, table: str, snapshot_id: int) -> None:
        from pyiceberg.exceptions import NoSuchTableError

        catalog = self._cat()
        namespace, name = _split_identifier(table)
        try:
            tbl = catalog.load_table((namespace, name))
        except NoSuchTableError:
            return
        # pyiceberg maintenance: expire a specific snapshot id
        tbl.expire_snapshots().expire_snapshot_id(snapshot_id).commit()

    async def drop_table(self, table: str) -> None:
        await asyncio.to_thread(self._drop_table_sync, table)

    def _drop_table_sync(self, table: str) -> None:
        from pyiceberg.exceptions import NoSuchTableError

        catalog = self._cat()
        namespace, name = _split_identifier(table)
        try:
            catalog.drop_table((namespace, name))
        except NoSuchTableError:
            pass

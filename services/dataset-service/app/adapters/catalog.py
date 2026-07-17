"""Catalog implementations.

LocalCatalog is the dev/test implementation of the Iceberg `Catalog` port:
table metadata is a JSON file, snapshots are parquet files. The real Iceberg
REST catalog adapter is stubbed below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.domain.errors import NotFound


class LocalCatalog:
    """Local metadata + parquet files standing in for an Iceberg catalog."""

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _table_dir(self, table: str) -> Path:
        return self.base / table.replace("/", "_")

    def _meta_path(self, table: str) -> Path:
        return self._table_dir(table) / "metadata.json"

    def _read_meta(self, table: str) -> dict:
        path = self._meta_path(table)
        if not path.exists():
            return {"table": table, "snapshots": {}}
        return json.loads(path.read_text())

    def _write_meta(self, table: str, meta: dict) -> None:
        path = self._meta_path(table)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2))

    # -- helper used by tests/producers to commit data (ingestion side in prod)
    async def commit_snapshot(self, table: str, snapshot_id: int, df: pd.DataFrame) -> None:
        meta = self._read_meta(table)
        data_file = self._table_dir(table) / f"snap-{snapshot_id}.parquet"
        data_file.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(data_file)
        meta["snapshots"][str(snapshot_id)] = {"file": data_file.name, "expired": False}
        self._write_meta(table, meta)

    async def snapshot_exists(self, table: str, snapshot_id: int) -> bool:
        snap = self._read_meta(table)["snapshots"].get(str(snapshot_id))
        return bool(snap and not snap["expired"])

    async def read_snapshot(self, table: str, snapshot_id: int) -> pd.DataFrame:
        snap = self._read_meta(table)["snapshots"].get(str(snapshot_id))
        if not snap or snap["expired"]:
            raise NotFound(f"snapshot {snapshot_id} not found in {table}")
        return pd.read_parquet(self._table_dir(table) / snap["file"])

    async def read_snapshot_head(
        self, table: str, snapshot_id: int, max_rows: int
    ) -> pd.DataFrame:
        """Read at most ``max_rows`` rows via pyarrow batch iteration so the
        paged browse/read path never materializes a whole large snapshot."""
        import pyarrow.parquet as pq

        snap = self._read_meta(table)["snapshots"].get(str(snapshot_id))
        if not snap or snap["expired"]:
            raise NotFound(f"snapshot {snapshot_id} not found in {table}")
        pf = pq.ParquetFile(self._table_dir(table) / snap["file"])
        if max_rows <= 0:
            return pf.schema_arrow.empty_table().to_pandas()
        batches = []
        seen = 0
        for batch in pf.iter_batches(batch_size=min(max_rows, 65_536)):
            batches.append(batch)
            seen += batch.num_rows
            if seen >= max_rows:
                break
        import pyarrow as pa

        if not batches:
            return pf.schema_arrow.empty_table().to_pandas()
        return pa.Table.from_batches(batches).slice(0, max_rows).to_pandas()

    async def expire_snapshot(self, table: str, snapshot_id: int) -> None:
        meta = self._read_meta(table)
        snap = meta["snapshots"].get(str(snapshot_id))
        if not snap:
            return
        snap["expired"] = True
        data_file = self._table_dir(table) / snap["file"]
        if data_file.exists():
            data_file.unlink()
        self._write_meta(table, meta)

    async def drop_table(self, table: str) -> None:
        table_dir = self._table_dir(table)
        if table_dir.exists():
            for child in table_dir.iterdir():
                child.unlink()
            table_dir.rmdir()

    async def data_file_uris(self, table: str, snapshot_id: int | None = None) -> list[str]:
        """Local parquet files standing in for Iceberg data files. If snapshot_id
        is None, resolve to the most recent live snapshot."""
        meta = self._read_meta(table)
        snaps = meta["snapshots"]
        if snapshot_id is None:
            live = [sid for sid, s in snaps.items() if not s["expired"]]
            if not live:
                return []
            snapshot_id = live[-1]
        snap = snaps.get(str(snapshot_id))
        if not snap or snap["expired"]:
            return []
        return [str(self._table_dir(table) / snap["file"])]

    async def browse_snapshot(
        self, table: str, snapshot_id: int, *, filters, sort_col, sort_dir, offset, limit
    ):
        """Engine-pushed browse (filter/sort/count/page in DuckDB over the local
        parquet). Returns (columns, page_rows, total, filtered)."""
        import asyncio

        from app.adapters.duckdb_browse import browse_parquet

        snap = self._read_meta(table)["snapshots"].get(str(snapshot_id))
        if not snap or snap["expired"]:
            raise NotFound(f"snapshot {snapshot_id} not found in {table}")
        path = str(self._table_dir(table) / snap["file"])
        return await asyncio.to_thread(
            browse_parquet, source_uris=[path], filters=filters,
            sort_col=sort_col, sort_dir=sort_dir, offset=offset, limit=limit,
        )

    async def table_columns(self, table: str) -> list[dict[str, str]]:
        meta = self._read_meta(table)
        live = [s for s in meta["snapshots"].values() if not s["expired"]]
        if not live:
            return []
        df = pd.read_parquet(self._table_dir(table) / live[-1]["file"])
        return [{"name": str(c), "type": str(df.dtypes[c])} for c in df.columns]


class IcebergRestCatalog:
    """Real Iceberg REST catalog adapter via the shared ``windrose_common``
    pyiceberg client. Snapshot verification hits the catalog directly (BR-1 —
    never trust the caller); reads time-travel to a snapshot id; expiry uses the
    pyiceberg maintenance API. Runtime catalog."""

    def __init__(
        self,
        catalog_uri: str = "http://localhost:8181",
        *,
        warehouse: str = "s3://windrose-warehouse/",
        s3_endpoint: str = "http://localhost:9000",
        s3_access_key: str = "windrose",
        s3_secret_key: str = "windrose_dev",
        s3_region: str = "us-east-1",
    ):
        from windrose_common.iceberg import IcebergConfig
        from windrose_common.iceberg import IcebergRestCatalog as _Catalog

        self._catalog = _Catalog(
            IcebergConfig(
                uri=catalog_uri,
                warehouse=warehouse,
                s3_endpoint=s3_endpoint,
                s3_access_key=s3_access_key,
                s3_secret_key=s3_secret_key,
                s3_region=s3_region,
            )
        )
        # kept so DuckDB (httpfs) can read the snapshot's s3 data files directly
        # for the engine-pushed browse.
        self._s3 = {
            "endpoint": s3_endpoint,
            "access_key": s3_access_key,
            "secret_key": s3_secret_key,
            "region": s3_region,
        }

    async def snapshot_exists(self, table: str, snapshot_id: int) -> bool:
        return await self._catalog.snapshot_exists(table, snapshot_id)

    async def read_snapshot(self, table: str, snapshot_id: int) -> pd.DataFrame:
        return await self._catalog.read_snapshot(table, snapshot_id)

    async def read_snapshot_head(
        self, table: str, snapshot_id: int, max_rows: int
    ) -> pd.DataFrame:
        return await self._catalog.read_snapshot_head(table, snapshot_id, max_rows)

    async def expire_snapshot(self, table: str, snapshot_id: int) -> None:
        await self._catalog.expire_snapshot(table, snapshot_id)

    async def drop_table(self, table: str) -> None:
        await self._catalog.drop_table(table)

    async def data_file_uris(self, table: str, snapshot_id: int | None = None) -> list[str]:
        return await self._catalog.data_file_uris(table, snapshot_id)

    async def browse_snapshot(
        self, table: str, snapshot_id: int, *, filters, sort_col, sort_dir, offset, limit
    ):
        """Engine-pushed browse over the snapshot's s3 parquet data files via
        DuckDB+httpfs. Returns (columns, page_rows, total, filtered)."""
        import asyncio

        from app.adapters.duckdb_browse import browse_parquet
        from app.domain.errors import NotFound

        uris = await self.data_file_uris(table, snapshot_id)
        if not uris:
            raise NotFound(f"snapshot {snapshot_id} not found in {table}")
        return await asyncio.to_thread(
            browse_parquet, source_uris=uris, filters=filters,
            sort_col=sort_col, sort_dir=sort_dir, offset=offset, limit=limit, s3=self._s3,
        )

    async def table_columns(self, table: str) -> list[dict[str, str]]:
        return await self._catalog.table_columns(table)

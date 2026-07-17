"""Unit tests for the object-store / data-lake SOURCE engine (ING-FR-064).

Covers the connector configs (new ``file_format`` / ``glob`` fields), the
glob + incremental-mtime selection logic, the preview/fetch/ingest pipeline
against an in-memory fake backend client, and — crucially — that the incremental
watermark is a **typed ``datetime`` bound out-of-band**, never spliced into any
listing request (BR-5, ING-FR-061). The GCS and Azure Blob SDK clients are
contract-tested with an injected fake SDK object (no SDK install, no network).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from app.domain.connectors import dump_config, validate_config
from app.domain.drivers.objectsource import (
    ObjectRef,
    ObjectSourceIngestor,
    ObjectStoreProber,
    ObjectStoreSourceFetcher,
    ObjectStoreSourcePreviewer,
    coerce_since,
    match_glob,
    newest_mtime,
    select_objects,
)
from app.domain.objectstore import LocalFSObjectStore
from app.domain.tablewriter import ParquetFileTableWriter

# --------------------------------------------------------------------------- fakes

_MT = lambda d: datetime(2026, 7, d, tzinfo=UTC)  # noqa: E731


class _Body(io.BytesIO):
    """A .read(n)/.close() streaming body over bytes."""


class FakeObjectStoreClient:
    """In-memory object store backend. Records every list/read call so tests can
    prove the watermark value never enters a request argument."""

    def __init__(self, objects: dict[str, tuple[bytes, datetime | None]]) -> None:
        self.objects = objects
        self.list_calls: list[str] = []
        self.read_calls: list[str] = []
        self.closed = False

    def probe(self) -> None:
        return None

    def list_objects(self, prefix: str) -> list[ObjectRef]:
        self.list_calls.append(prefix)
        return [
            ObjectRef(key=k, size=len(v[0]), last_modified=v[1])
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

    def open_read(self, key: str):
        self.read_calls.append(key)
        return _Body(self.objects[key][0])

    def close(self) -> None:
        self.closed = True


def _factory_for(client: FakeObjectStoreClient):
    return lambda config, secrets, timeout: client


# =========================================================================== configs


@pytest.mark.parametrize("ctype", ["s3", "gcs", "azure_blob", "ftp"])
def test_object_configs_accept_file_format_and_glob(ctype: str) -> None:
    base = {
        "s3": {"bucket": "b"},
        "gcs": {"project_id": "p", "bucket": "b"},
        "azure_blob": {"account_name": "a", "container_name": "c"},
        "ftp": {"host": "h", "username": "u"},
    }[ctype]
    model = validate_config(ctype, {**base, "file_format": "parquet", "glob": "*.parquet"})
    dumped = dump_config(model)
    assert dumped["file_format"] == "parquet"
    assert dumped["glob"] == "*.parquet"


def test_object_config_rejects_bad_file_format() -> None:
    from app.domain.errors import ValidationFailedError

    with pytest.raises(ValidationFailedError):
        validate_config("s3", {"bucket": "b", "file_format": "orc"})


# =========================================================================== selection


def test_match_glob_key_and_basename() -> None:
    assert match_glob("data/2026/part-1.parquet", "*.parquet")
    assert match_glob("data/2026/part-1.parquet", "data/2026/*.parquet")
    assert not match_glob("data/part-1.csv", "*.parquet")
    assert match_glob("anything", None)  # no glob → match all


def test_select_objects_glob_and_directory_markers() -> None:
    refs = [
        ObjectRef("p/", 0, _MT(1)),  # directory marker → skipped
        ObjectRef("p/a.csv", 10, _MT(2)),
        ObjectRef("p/b.parquet", 20, _MT(3)),
    ]
    got = select_objects(refs, glob="*.parquet")
    assert [r.key for r in got] == ["p/b.parquet"]


def test_select_objects_incremental_is_typed_datetime_comparison() -> None:
    refs = [
        ObjectRef("p/old.csv", 10, _MT(1)),
        ObjectRef("p/mid.csv", 10, _MT(3)),
        ObjectRef("p/new.csv", 10, _MT(5)),
    ]
    since = coerce_since("2026-07-02T00:00:00Z")  # typed → datetime
    assert isinstance(since, datetime)
    got = select_objects(refs, since=since)
    # only objects strictly newer than the typed watermark, oldest-first
    assert [r.key for r in got] == ["p/mid.csv", "p/new.csv"]
    assert newest_mtime(got) == _MT(5)


def test_coerce_since_rejects_untyped_garbage() -> None:
    assert coerce_since(None) is None
    assert coerce_since(_MT(1)) == _MT(1)
    with pytest.raises(ValueError):
        coerce_since("not-a-timestamp")


async def test_incremental_watermark_never_spliced_into_list_request() -> None:
    """BR-5 / ING-FR-061: the mtime watermark is a typed value compared
    client-side; it must NEVER appear in any listing request argument."""
    client = FakeObjectStoreClient(
        {
            "lake/old.csv": (b"id\n1\n", _MT(1)),
            "lake/new.csv": (b"id\n2\n", _MT(9)),
        }
    )
    fetcher = ObjectStoreSourceFetcher(_factory_for(client))
    cfg = validate_config("s3", {"bucket": "b", "root_prefix": "lake/", "glob": "*.csv"})

    since = coerce_since("2026-07-05T00:00:00Z")
    refs = await fetcher.list_objects(cfg, {}, since=since)
    assert [r.key for r in refs] == ["lake/new.csv"]

    # the list request carried only the prefix — no time value anywhere
    assert client.list_calls == ["lake/"]
    watermark_text = "2026-07-05"
    assert all(watermark_text not in call for call in client.list_calls)


# =========================================================================== preview / fetch


async def test_preview_decodes_first_object_rows() -> None:
    client = FakeObjectStoreClient(
        {"lake/a.csv": (b"id,name\n1,alpha\n2,beta\n3,gamma\n", _MT(1))}
    )
    previewer = ObjectStoreSourcePreviewer(_factory_for(client))
    cfg = validate_config("s3", {"bucket": "b", "root_prefix": "lake/", "file_format": "csv"})
    result = await previewer.preview(cfg, {}, {}, limit=2)
    assert result.columns == ["id", "name"]
    assert result.rows == [{"id": "1", "name": "alpha"}, {"id": "2", "name": "beta"}]


async def test_fetch_single_object_streams_to_object_store(tmp_path) -> None:
    payload = b"col_a,col_b\n" + b"x,y\n" * 1000
    client = FakeObjectStoreClient({"lake/data.csv": (payload, _MT(1))})
    fetcher = ObjectStoreSourceFetcher(_factory_for(client))
    cfg = validate_config("s3", {"bucket": "b"})
    store = LocalFSObjectStore(tmp_path / "objects")

    result = await fetcher.fetch(cfg, {}, {"key": "lake/data.csv"}, store, "s3/data.csv")
    assert result.size == len(payload)
    read = b""
    async for chunk in store.open_stream("s3/data.csv"):
        read += chunk
    assert read == payload
    assert client.closed


async def test_probe_ok_and_failure() -> None:
    client = FakeObjectStoreClient({})
    prober = ObjectStoreProber(_factory_for(client))
    cfg = validate_config("s3", {"bucket": "b"})
    assert (await prober.probe(cfg, {})).status == "ok"

    def _boom(config, secrets, timeout):
        raise RuntimeError("Access Denied")

    bad = ObjectStoreProber(_boom)
    res = await bad.probe(cfg, {})
    assert res.status == "failed"
    assert res.error_category == "AUTH_FAILED"


# =========================================================================== ingest pipeline


async def test_ingest_lands_rows_across_objects_single_snapshot(tmp_path) -> None:
    """Full pipeline: list → glob filter → stream-decode multiple objects →
    exactly one bronze snapshot with the combined row count (BR-9)."""
    client = FakeObjectStoreClient(
        {
            "lake/skip.txt": (b"ignore me", _MT(1)),  # filtered out by glob
            "lake/a.csv": (b"id,name\n1,alpha\n2,beta\n", _MT(2)),
            "lake/b.csv": (b"id,name\n3,gamma\n", _MT(4)),
        }
    )
    ingestor = ObjectSourceIngestor(_factory_for(client))
    cfg = validate_config(
        "s3", {"bucket": "b", "root_prefix": "lake/", "glob": "*.csv", "file_format": "csv"}
    )
    writer = ParquetFileTableWriter(tmp_path / "bronze")

    result = await ingestor.ingest(
        cfg, {}, table_writer=writer, table="bronze.t.ds_1", ingestion_id="ing-1"
    )
    assert result.rows == 3
    assert result.objects == 2
    assert result.new_watermark == "2026-07-04T00:00:00+00:00"  # max mtime, serialized

    snaps = writer.snapshots("bronze.t.ds_1")
    assert len(snaps) == 1
    assert snaps[0]["summary"]["ingestion_id"] == "ing-1"
    assert snaps[0]["summary"]["source"] == "object:s3"


async def test_ingest_incremental_only_new_objects(tmp_path) -> None:
    client = FakeObjectStoreClient(
        {
            "lake/old.csv": (b"id\n1\n", _MT(1)),
            "lake/new.csv": (b"id\n2\n", _MT(9)),
        }
    )
    ingestor = ObjectSourceIngestor(_factory_for(client))
    cfg = validate_config("s3", {"bucket": "b", "root_prefix": "lake/", "file_format": "csv"})
    writer = ParquetFileTableWriter(tmp_path / "bronze")

    result = await ingestor.ingest(
        cfg,
        {},
        table_writer=writer,
        table="bronze.t.ds_2",
        ingestion_id="ing-2",
        since=coerce_since("2026-07-05T00:00:00Z"),
    )
    assert result.objects == 1  # only the newer object ingested
    assert result.rows == 1
    assert result.new_watermark == "2026-07-09T00:00:00+00:00"


# =========================================================================== gcs/azure contracts


class _FakeGcsBlob:
    def __init__(self, name: str, data: bytes, updated: datetime) -> None:
        self.name = name
        self.size = len(data)
        self.updated = updated
        self._data = data

    def open(self, mode: str):
        return io.BytesIO(self._data)


class _FakeGcsClient:
    def __init__(self, blobs: list[_FakeGcsBlob]) -> None:
        self._blobs = blobs

    def list_blobs(self, bucket, prefix: str | None = None, max_results=None):
        out = [b for b in self._blobs if prefix is None or b.name.startswith(prefix)]
        return out[:max_results] if max_results else out

    def bucket(self, name):
        client = self

        class _B:
            def blob(self, key):
                return next(b for b in client._blobs if b.name == key)

        return _B()


def test_gcs_client_list_and_read_shaping() -> None:
    from app.domain.drivers.gcs import GcsObjectStoreClient

    blobs = [_FakeGcsBlob("lake/a.csv", b"id\n1\n", _MT(2))]
    cfg = validate_config("gcs", {"project_id": "p", "bucket": "b"})
    client = GcsObjectStoreClient(cfg, {}, 15.0, _client=_FakeGcsClient(blobs))
    refs = client.list_objects("lake/")
    assert [(r.key, r.size, r.last_modified) for r in refs] == [("lake/a.csv", 5, _MT(2))]
    assert client.open_read("lake/a.csv").read(100) == b"id\n1\n"
    client.probe()  # does not raise


class _FakeAzureDownloader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def chunks(self):
        yield self._data[:3]
        yield self._data[3:]


class _FakeAzureBlobProps:
    def __init__(self, name: str, data: bytes, last_modified: datetime) -> None:
        self.name = name
        self.size = len(data)
        self.last_modified = last_modified


class _FakeAzureContainer:
    def __init__(self, blobs: dict[str, bytes], mtimes: dict[str, datetime]) -> None:
        self._blobs = blobs
        self._mtimes = mtimes

    def list_blobs(self, name_starts_with=None):
        for name, data in self._blobs.items():
            if name_starts_with is None or name.startswith(name_starts_with):
                yield _FakeAzureBlobProps(name, data, self._mtimes[name])

    def download_blob(self, key):
        return _FakeAzureDownloader(self._blobs[key])

    def get_container_properties(self):
        return {"name": "c"}


class _FakeAzureService:
    def __init__(self, container: _FakeAzureContainer) -> None:
        self._container = container

    def get_container_client(self, name):
        return self._container


def test_azure_client_list_and_chunked_read_shaping() -> None:
    from app.domain.drivers.azure_blob import AzureBlobObjectStoreClient

    container = _FakeAzureContainer(
        {"lake/a.csv": b"id\n1\n2\n"}, {"lake/a.csv": _MT(3)}
    )
    cfg = validate_config("azure_blob", {"account_name": "a", "container_name": "c"})
    client = AzureBlobObjectStoreClient(cfg, {}, 15.0, _service=_FakeAzureService(container))
    refs = client.list_objects("lake/")
    assert [(r.key, r.last_modified) for r in refs] == [("lake/a.csv", _MT(3))]
    body = client.open_read("lake/a.csv")
    # chunked reader reassembles the full object via .read(n)
    assert body.read(2) == b"id"
    assert body.read(100) == b"\n1\n2\n"
    client.probe()  # does not raise

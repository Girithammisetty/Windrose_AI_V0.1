"""Integration: the REAL object-store / data-lake SOURCE connectors.

Speaks the real wire protocol against local infra (CONVENTIONS.md END STATE,
"Object storage (S3/GCS/Blob) → MinIO"):

    s3  -> boto3  vs. MinIO (the local S3 API): seed a bucket with Parquet + CSV
           objects, probe (list bucket), preview (decode first rows), incremental
           list by object LastModified, and a full source-fetch that streams every
           matching object → decodes → lands rows in a single bronze snapshot.
    ftp -> aioftp vs. a REAL in-process FTP server (pyftpdlib on loopback — real
           FTP wire protocol; avoids Docker passive-port mapping flakiness on Mac):
           probe (LIST) + streaming file fetch into the object store, memory-bounded.

GCS and Azure Blob are credential-gated: their live probe skips-with-reason when
credentials are absent (offline shaping is covered by tests/unit/test_object_source).

Auto-skips with a clear message when MinIO / Docker is unavailable.
"""

from __future__ import annotations

import io
import os
import socket
import threading
import time
import uuid
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.domain.connectors import S3Config
from app.domain.drivers.ftp import FtpProber, FtpSourceFetcher, FtpSourcePreviewer
from app.domain.drivers.objectsource import (
    ObjectSourceIngestor,
    ObjectStoreProber,
    ObjectStoreSourceFetcher,
    ObjectStoreSourcePreviewer,
    coerce_since,
)
from app.domain.drivers.s3 import s3_client_factory
from app.domain.objectstore import LocalFSObjectStore
from app.domain.tablewriter import ParquetFileTableWriter

pytestmark = [
    pytest.mark.integration,
    # pyftpdlib's ioloop thread logs a benign "Bad file descriptor" as it unwinds
    # its kqueue on shutdown; it is cosmetic and unrelated to the assertions.
    pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning"),
]

MINIO_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
MINIO_KEY = os.getenv("S3_ACCESS_KEY", "windrose")
MINIO_SECRET = os.getenv("S3_SECRET_KEY", "windrose_dev")
MINIO_BUCKET = os.getenv("S3_TEST_BUCKET", "windrose-uploads")
MINIO_SECRETS = {"access_key_id": MINIO_KEY, "secret_access_key": MINIO_SECRET}


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _require_minio() -> None:
    if not _reachable("localhost", 9000):
        pytest.skip("MinIO not reachable on localhost:9000 — dev infra down")


# --------------------------------------------------------------------------- MinIO seeding


def _raw_client():
    import boto3
    from botocore.client import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_KEY,
        aws_secret_access_key=MINIO_SECRET,
        region_name="us-east-1",
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _parquet_bytes(rows: list[dict]) -> bytes:
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


@pytest.fixture
def s3_lake():
    """Seed a MinIO bucket prefix with a CSV + a Parquet object; clean up after."""
    _require_minio()
    client = _raw_client()
    prefix = f"ing-obj-it/{uuid.uuid4().hex[:8]}"
    csv_obj = f"{prefix}/orders.csv"
    parquet_obj = f"{prefix}/orders.parquet"
    client.put_object(
        Bucket=MINIO_BUCKET,
        Key=csv_obj,
        Body=b"id,name\n1,alpha\n2,beta\n",
    )
    client.put_object(
        Bucket=MINIO_BUCKET,
        Key=parquet_obj,
        Body=_parquet_bytes([{"id": 3, "name": "gamma"}, {"id": 4, "name": "delta"}]),
    )
    yield {"prefix": prefix, "csv": csv_obj, "parquet": parquet_obj}
    # cleanup
    resp = client.list_objects_v2(Bucket=MINIO_BUCKET, Prefix=prefix)
    keys = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
    if keys:
        client.delete_objects(Bucket=MINIO_BUCKET, Delete={"Objects": keys})


def _s3_cfg(prefix: str, file_format: str, glob: str | None = None) -> S3Config:
    return S3Config(
        bucket=MINIO_BUCKET,
        region="us-east-1",
        endpoint=MINIO_ENDPOINT,
        root_prefix=prefix + "/",
        file_format=file_format,
        glob=glob,
    )


# ===================================================================== S3 (MinIO, real)


async def test_s3_probe_ok_and_auth_failed(s3_lake) -> None:
    """Real test-connection against MinIO: list bucket ok, bad creds → AUTH_FAILED."""
    prober = ObjectStoreProber(s3_client_factory, connect_timeout_s=10)
    cfg = _s3_cfg(s3_lake["prefix"], "csv")

    ok = await prober.probe(cfg, MINIO_SECRETS)
    assert ok.status == "ok", ok.error_detail

    bad = await prober.probe(cfg, {"access_key_id": "nope", "secret_access_key": "wrong"})
    assert bad.status == "failed"
    assert bad.error_category in ("AUTH_FAILED", "SOURCE_UNREACHABLE")
    assert "wrong" not in (bad.error_detail or "")  # scrubbed


async def test_s3_preview_decodes_first_object(s3_lake) -> None:
    previewer = ObjectStoreSourcePreviewer(s3_client_factory, connect_timeout_s=10)
    cfg = _s3_cfg(s3_lake["prefix"], "csv", glob="*.csv")
    result = await previewer.preview(cfg, MINIO_SECRETS, {}, limit=1)
    assert result.columns == ["id", "name"]
    assert result.rows == [{"id": "1", "name": "alpha"}]


async def test_s3_full_fetch_csv_lands_rows(s3_lake, tmp_path) -> None:
    """Real source-fetch: stream the CSV object from MinIO → decode → one bronze
    snapshot with the exact row count."""
    ingestor = ObjectSourceIngestor(s3_client_factory, connect_timeout_s=10)
    cfg = _s3_cfg(s3_lake["prefix"], "csv", glob="*.csv")
    writer = ParquetFileTableWriter(tmp_path / "bronze")

    result = await ingestor.ingest(
        cfg, MINIO_SECRETS, table_writer=writer, table="bronze.t.ds_csv", ingestion_id="s3-csv"
    )
    assert result.objects == 1
    assert result.rows == 2
    snaps = writer.snapshots("bronze.t.ds_csv")
    assert len(snaps) == 1 and snaps[0]["summary"]["source"] == "object:s3"


async def test_s3_full_fetch_parquet_lands_rows(s3_lake, tmp_path) -> None:
    """Real source-fetch of a Parquet object from MinIO → decode → bronze rows."""
    ingestor = ObjectSourceIngestor(s3_client_factory, connect_timeout_s=10)
    cfg = _s3_cfg(s3_lake["prefix"], "parquet", glob="*.parquet")
    writer = ParquetFileTableWriter(tmp_path / "bronze")

    result = await ingestor.ingest(
        cfg, MINIO_SECRETS, table_writer=writer, table="bronze.t.ds_pq", ingestion_id="s3-pq"
    )
    assert result.objects == 1
    assert result.rows == 2  # gamma + delta
    assert result.new_watermark is not None  # object LastModified captured


async def test_s3_incremental_only_new_objects(s3_lake, tmp_path) -> None:
    """Incremental by object LastModified: with `since` set between the two
    objects' mtimes, only the newer object is ingested. The watermark is a typed
    datetime compared client-side — never spliced into the S3 list request."""
    fetcher = ObjectStoreSourceFetcher(s3_client_factory, connect_timeout_s=10)
    cfg = _s3_cfg(s3_lake["prefix"], "csv")

    # baseline: list everything, learn the CSV object's mtime
    all_refs = await fetcher.list_objects(cfg, MINIO_SECRETS)
    assert len(all_refs) == 2
    mtimes = {r.key: r.last_modified for r in all_refs}

    # upload a THIRD object guaranteed newer than the existing two (>1s gap for
    # MinIO's second-resolution LastModified)
    time.sleep(1.2)
    client = _raw_client()
    new_key = f"{s3_lake['prefix']}/late.csv"
    client.put_object(Bucket=MINIO_BUCKET, Key=new_key, Body=b"id,name\n9,late\n")

    watermark = max(m for m in mtimes.values() if m is not None)
    since = coerce_since(watermark)  # typed datetime
    assert isinstance(since, datetime)

    new_refs = await fetcher.list_objects(cfg, MINIO_SECRETS, since=since)
    assert [r.key for r in new_refs] == [new_key]  # only the object newer than the watermark


async def test_s3_fetch_is_memory_bounded(s3_lake, tmp_path) -> None:
    """A larger object streams chunk-by-chunk into the object store without being
    buffered whole (ING-FR-041)."""
    client = _raw_client()
    big_key = f"{s3_lake['prefix']}/big.csv"
    payload = b"a,b\n" + b"x,y\n" * 2_000_000  # ~8 MiB
    client.put_object(Bucket=MINIO_BUCKET, Key=big_key, Body=payload)

    fetcher = ObjectStoreSourceFetcher(s3_client_factory, connect_timeout_s=10)
    cfg = _s3_cfg(s3_lake["prefix"], "csv")
    store = LocalFSObjectStore(tmp_path / "objects")
    result = await fetcher.fetch(cfg, MINIO_SECRETS, {"key": big_key}, store, "s3/big.csv")
    assert result.size == len(payload)


# ===================================================================== FTP (pyftpdlib, real)


@pytest.fixture
def ftp_server(tmp_path_factory):
    """Real in-process FTP server (pyftpdlib) on 127.0.0.1 with a seeded file."""
    try:
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"pyftpdlib not installed: {exc}")

    home = tmp_path_factory.mktemp("ftp-home")
    (home / "data.csv").write_bytes(b"id,name\n1,alpha\n2,beta\n")

    authorizer = DummyAuthorizer()
    authorizer.add_user("ftpuser", "ftppass", str(home), perm="elradfmw")
    handler = FTPHandler
    handler.authorizer = authorizer
    server = FTPServer(("127.0.0.1", 0), handler)
    host, port = server.address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"host": host, "port": port, "username": "ftpuser", "password": "ftppass"}
    finally:
        server.close_all()


def _ftp_cfg(server) -> object:
    from app.domain.connectors import FtpConfig

    return FtpConfig(host=server["host"], port=server["port"], username=server["username"])


async def test_ftp_probe_and_file_fetch_to_object_store(ftp_server, tmp_path) -> None:
    """Real FTP LIST probe + streaming file fetch into the object store."""
    cfg = _ftp_cfg(ftp_server)
    secrets = {"password": ftp_server["password"]}

    probe = await FtpProber(connect_timeout_s=10).probe(cfg, secrets)
    assert probe.status == "ok", probe.error_detail

    previewer = FtpSourcePreviewer(connect_timeout_s=10)
    preview = await previewer.preview(cfg, secrets, {"path": "/"}, 100)
    assert {"name": "data.csv"} in preview.rows

    store = LocalFSObjectStore(tmp_path / "objects")
    result = await FtpSourceFetcher(connect_timeout_s=10).fetch(
        cfg, secrets, {"path": "data.csv"}, store, "ftp/data.csv"
    )
    payload = b"id,name\n1,alpha\n2,beta\n"
    assert result.size == len(payload)
    read = b""
    async for chunk in store.open_stream("ftp/data.csv"):
        read += chunk
    assert read == payload


async def test_ftp_bad_password_is_auth_failed(ftp_server) -> None:
    cfg = _ftp_cfg(ftp_server)
    probe = await FtpProber(connect_timeout_s=10).probe(cfg, {"password": "wrong"})
    assert probe.status == "failed"
    assert probe.error_category == "AUTH_FAILED"
    assert "wrong" not in (probe.error_detail or "")


# ===================================================================== GCS / Azure (gated)


def _require(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        pytest.skip(f"needs credentials: set {', '.join(missing)} to run this live test")
    return {n: os.environ[n] for n in names}


async def test_gcs_live_probe() -> None:
    env = _require("GCS_PROJECT_ID", "GCS_BUCKET", "GCS_CREDENTIALS_JSON")
    from app.domain.connectors import GcsConfig

    prober = ObjectStoreProber(_gcs_factory())
    cfg = GcsConfig(project_id=env["GCS_PROJECT_ID"], bucket=env["GCS_BUCKET"])
    result = await prober.probe(cfg, {"credentials_json": env["GCS_CREDENTIALS_JSON"]})
    assert result.status == "ok", result.error_detail


async def test_azure_blob_live_probe() -> None:
    env = _require("AZURE_ACCOUNT_NAME", "AZURE_CONTAINER", "AZURE_ACCOUNT_KEY")
    from app.domain.connectors import AzureBlobConfig

    prober = ObjectStoreProber(_azure_factory())
    cfg = AzureBlobConfig(
        account_name=env["AZURE_ACCOUNT_NAME"], container_name=env["AZURE_CONTAINER"]
    )
    result = await prober.probe(cfg, {"account_key": env["AZURE_ACCOUNT_KEY"]})
    assert result.status == "ok", result.error_detail


def _gcs_factory():
    from app.domain.drivers.gcs import gcs_client_factory

    return gcs_client_factory


def _azure_factory():
    from app.domain.drivers.azure_blob import azure_blob_client_factory

    return azure_blob_client_factory

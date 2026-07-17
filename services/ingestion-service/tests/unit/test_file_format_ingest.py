"""Real Parquet + Avro files ingested end-to-end through the upload pipeline.

Complements the streaming-decode unit tests (``test_decode_formats.py``) by
driving a genuine Parquet and a genuine Avro file all the way through the file
upload → complete → decode → stage → single Iceberg-append pipeline (inline
runner, memory-tier ParquetFileTableWriter), asserting the job completes and the
row count matches the source (ING-FR-021, BR-2).
"""

from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq
from fastavro import parse_schema
from fastavro import writer as avro_writer

from tests.util import upload_file_flow


def _parquet_bytes(n: int) -> bytes:
    table = pa.table(
        {
            "id": list(range(n)),
            "name": [f"claim-{i}" for i in range(n)],
            "amount": [float(i) * 1.5 for i in range(n)],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _avro_bytes(n: int) -> bytes:
    schema = parse_schema(
        {
            "type": "record",
            "name": "Claim",
            "fields": [
                {"name": "id", "type": "int"},
                {"name": "name", "type": "string"},
                {"name": "amount", "type": "double"},
            ],
        }
    )
    buf = io.BytesIO()
    avro_writer(
        buf,
        schema,
        [{"id": i, "name": f"claim-{i}", "amount": float(i) * 1.5} for i in range(n)],
    )
    return buf.getvalue()


async def test_parquet_file_ingests_to_completed(client, auth_a) -> None:
    data = _parquet_bytes(250)
    job = await upload_file_flow(client, auth_a, data, part_size=8192, file_format="parquet")
    assert job["status"] == "completed", job
    assert job["rows_appended"] == 250


async def test_avro_file_ingests_to_completed(client, auth_a) -> None:
    data = _avro_bytes(180)
    job = await upload_file_flow(client, auth_a, data, part_size=8192, file_format="avro")
    assert job["status"] == "completed", job
    assert job["rows_appended"] == 180

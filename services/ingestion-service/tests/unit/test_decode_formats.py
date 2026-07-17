"""Streaming format decoding (ING-FR-021, BR-3/4, AC-13 mechanics)."""

from __future__ import annotations

import io
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastavro import parse_schema, writer

from app.domain.decode import MAX_SAMPLES, DecodeOptions, DecodeStats, decode_stream
from app.domain.errors import ErrorCategory, PermanentJobError


async def _stream(data: bytes, chunk: int = 7):  # tiny chunks to stress incremental parsing
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


async def collect(data: bytes, fmt: str, **opts):
    stats = DecodeStats()
    options = DecodeOptions(file_format=fmt, batch_size=opts.pop("batch_size", 3), **opts)
    rows = []
    columns = None
    async for batch in decode_stream(_stream(data), options, stats):
        columns = batch.columns
        rows.extend(batch.rows)
    return columns, rows, stats


async def test_csv_basic_and_batching() -> None:
    data = b"id,name\n1,a\n2,b\n3,c\n4,d\n"
    columns, rows, stats = await collect(data, "csv")
    assert columns == ["id", "name"]
    assert rows == [["1", "a"], ["2", "b"], ["3", "c"], ["4", "d"]]
    assert stats.rows_ok == 4 and stats.rows_bad == 0


async def test_csv_blank_headers_autonamed_br4() -> None:
    data = b"id,,\n1,a,b\n"
    columns, rows, stats = await collect(data, "csv")
    assert columns == ["id", "col_2", "col_3"]
    assert stats.renamed_columns == ["col_2", "col_3"]


async def test_csv_bad_rows_tolerated_up_to_limit() -> None:
    data = b"a,b\n1,2\nonly-one\n3,4\n1,2,3,4\n"
    columns, rows, stats = await collect(data, "csv", error_row_limit=10)
    assert rows == [["1", "2"], ["3", "4"]]
    assert stats.rows_bad == 2
    assert all("expected 2 columns" in s["reason"] for s in stats.bad_samples)


async def test_csv_row_limit_exceeded_with_truncated_samples() -> None:
    long_value = "v" * 999
    bad_lines = "\n".join(f"{long_value}" for _ in range(30))
    data = f"a,b\n{bad_lines}\n".encode()
    stats = DecodeStats()
    options = DecodeOptions(file_format="csv", error_row_limit=10, batch_size=100)
    with pytest.raises(PermanentJobError) as exc:
        async for _ in decode_stream(_stream(data), options, stats):
            pass
    assert exc.value.category == ErrorCategory.ROW_LIMIT_EXCEEDED
    assert len(exc.value.samples) <= MAX_SAMPLES
    assert all(len(s["raw"]) <= 256 for s in exc.value.samples)


async def test_tsv() -> None:
    columns, rows, _ = await collect(b"x\ty\n1\t2\n", "tsv")
    assert columns == ["x", "y"] and rows == [["1", "2"]]


async def test_jsonl_including_bad_lines() -> None:
    data = b'{"a": 1, "b": "x"}\nnot-json\n{"a": 2, "b": "y", "extra": true}\n'
    columns, rows, stats = await collect(data, "jsonl")
    assert columns == ["a", "b"]
    assert rows == [[1, "x"], [2, "y"]]
    assert stats.rows_bad == 1


async def test_json_array_incremental_parse() -> None:
    payload = [{"a": i, "b": f"v{i}"} for i in range(10)]
    data = json.dumps(payload).encode()
    columns, rows, stats = await collect(data, "json")
    assert columns == ["a", "b"]
    assert len(rows) == 10 and stats.rows_ok == 10


async def test_json_not_an_array_fails() -> None:
    stats = DecodeStats()
    with pytest.raises(PermanentJobError) as exc:
        async for _ in decode_stream(
            _stream(b'{"a": 1}'), DecodeOptions(file_format="json"), stats
        ):
            pass
    assert exc.value.category == ErrorCategory.DECODE_ERROR


async def test_parquet_roundtrip() -> None:
    table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    buf = io.BytesIO()
    pq.write_table(table, buf)
    columns, rows, stats = await collect(buf.getvalue(), "parquet")
    assert columns == ["a", "b"]
    assert rows == [[1, "x"], [2, "y"], [3, "z"]]
    assert stats.rows_ok == 3


async def test_avro_roundtrip() -> None:
    schema = parse_schema(
        {
            "type": "record",
            "name": "Rec",
            "fields": [{"name": "a", "type": "int"}, {"name": "b", "type": "string"}],
        }
    )
    buf = io.BytesIO()
    writer(buf, schema, [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    columns, rows, stats = await collect(buf.getvalue(), "avro")
    assert columns == ["a", "b"]
    assert rows == [[1, "x"], [2, "y"]]


async def test_invalid_parquet_is_decode_error() -> None:
    stats = DecodeStats()
    with pytest.raises(PermanentJobError) as exc:
        async for _ in decode_stream(
            _stream(b"definitely not parquet"), DecodeOptions(file_format="parquet"), stats
        ):
            pass
    assert exc.value.category == ErrorCategory.DECODE_ERROR


async def test_empty_and_header_only_yield_zero_rows() -> None:
    for data in (b"", b"id,name\n"):
        _columns, rows, stats = await collect(data, "csv")
        assert rows == [] and stats.rows_ok == 0  # BR-3 handled by the runner guard


async def test_xml_records_from_root_children() -> None:
    data = (
        b"<claims>"
        b"<claim><id>1</id><type>auto</type></claim>"
        b"<claim><id>2</id><type>health</type></claim>"
        b"<claim><id>3</id><type>property</type></claim>"
        b"</claims>"
    )
    columns, rows, stats = await collect(data, "xml")
    assert columns == ["id", "type"]
    assert rows == [["1", "auto"], ["2", "health"], ["3", "property"]]
    assert stats.rows_ok == 3 and stats.rows_bad == 0


async def test_xml_attributes_become_columns() -> None:
    data = b'<rows><row id="1" region="eu"><amount>10</amount></row></rows>'
    columns, rows, _ = await collect(data, "xml")
    assert columns == ["id", "region", "amount"]
    assert rows == [["1", "eu", "10"]]


async def test_xml_nested_elements_flattened() -> None:
    data = (
        b"<orders>"
        b"<order><id>7</id><customer><name>Acme</name><city>Zurich</city></customer></order>"
        b"</orders>"
    )
    columns, rows, _ = await collect(data, "xml")
    assert columns == ["id", "customer_name", "customer_city"]
    assert rows == [["7", "Acme", "Zurich"]]


async def test_xml_namespaced_tags_stripped() -> None:
    data = (
        b'<ns:claims xmlns:ns="http://x">'
        b"<ns:claim><ns:id>9</ns:id></ns:claim>"
        b"</ns:claims>"
    )
    columns, rows, _ = await collect(data, "xml")
    assert columns == ["id"]
    assert rows == [["9"]]


async def test_xml_missing_field_is_none() -> None:
    # columns fixed from the first record; a later record missing a field -> None
    data = (
        b"<items>"
        b"<item><a>1</a><b>x</b></item>"
        b"<item><a>2</a></item>"
        b"</items>"
    )
    columns, rows, stats = await collect(data, "xml")
    assert columns == ["a", "b"]
    assert rows == [["1", "x"], ["2", None]]
    assert stats.rows_ok == 2


async def test_xml_malformed_is_decode_error() -> None:
    stats = DecodeStats()
    with pytest.raises(PermanentJobError) as exc:
        async for _ in decode_stream(
            _stream(b"<claims><claim><id>1</id></claim>"), DecodeOptions(file_format="xml"), stats
        ):
            pass
    assert exc.value.category == ErrorCategory.DECODE_ERROR


async def test_xml_billion_laughs_dtd_rejected() -> None:
    # An uploaded XML declaring a DTD/entities (the entity-expansion DoS vector)
    # is refused before any content parse.
    payload = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [\n'
        b'  <!ENTITY lol "lol">\n'
        b'  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">\n'
        b']>\n'
        b"<claims><claim><id>&lol2;</id></claim></claims>"
    )
    stats = DecodeStats()
    with pytest.raises(PermanentJobError) as exc:
        async for _ in decode_stream(_stream(payload), DecodeOptions(file_format="xml"), stats):
            pass
    assert exc.value.category == ErrorCategory.DECODE_ERROR
    assert "DTD" in str(exc.value) or "entity" in str(exc.value).lower()

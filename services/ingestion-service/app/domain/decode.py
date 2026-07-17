"""Streaming format decoding (ING-FR-021, BR-2/3/4, AC-13).

Every decoder consumes an async byte stream and yields bounded RowBatches —
whole files are never buffered in memory. csv/tsv/json/jsonl decode fully
streaming; parquet/avro spool to a temp file on disk (bounded memory) and are
then read batch-wise.

Per-row decode failures are tolerated up to `error_row_limit`; up to 20 sample
bad rows are kept with values truncated to 256 chars (ING-FR-080).
"""

from __future__ import annotations

import csv
import json
import tempfile
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.errors import ErrorCategory, PermanentJobError
from app.domain.tablewriter import RowBatch

FILE_FORMATS: tuple[str, ...] = ("csv", "tsv", "json", "jsonl", "parquet", "avro", "xml")
MAX_SAMPLES = 20
SAMPLE_VALUE_TRUNC = 256


@dataclass(slots=True)
class DecodeOptions:
    file_format: str
    error_row_limit: int = 100
    batch_size: int = 5000


@dataclass(slots=True)
class DecodeStats:
    rows_ok: int = 0
    rows_bad: int = 0
    renamed_columns: list[str] = field(default_factory=list)  # BR-4 blank headers -> col_<n>
    bad_samples: list[dict[str, Any]] = field(default_factory=list)


def _truncate(value: Any) -> str:
    return str(value)[:SAMPLE_VALUE_TRUNC]


class _BadRowCollector:
    def __init__(self, limit: int, stats: DecodeStats) -> None:
        self.limit = limit
        self.stats = stats

    def add(self, row_number: int, raw: Any, reason: str) -> None:
        self.stats.rows_bad += 1
        if len(self.stats.bad_samples) < MAX_SAMPLES:
            self.stats.bad_samples.append(
                {"row_number": row_number, "raw": _truncate(raw), "reason": _truncate(reason)}
            )
        if self.stats.rows_bad > self.limit:
            raise PermanentJobError(
                ErrorCategory.ROW_LIMIT_EXCEEDED,
                f"bad rows ({self.stats.rows_bad}) exceeded error_row_limit ({self.limit})",
                samples=self.stats.bad_samples,
                hint="fix the malformed rows at the source or raise error_row_limit (max 10000)",
            )


async def _lines(chunks: AsyncIterator[bytes]) -> AsyncIterator[list[str]]:
    """Split a byte stream into batches of complete text lines."""
    buffer = b""
    async for chunk in chunks:
        buffer += chunk
        if b"\n" not in buffer:
            continue
        head, buffer = buffer.rsplit(b"\n", 1)
        yield head.decode("utf-8", errors="replace").splitlines()
    if buffer.strip():
        yield buffer.decode("utf-8", errors="replace").splitlines()


def _header_names(raw: list[str], stats: DecodeStats) -> list[str]:
    names = []
    for i, name in enumerate(raw, start=1):
        cleaned = name.strip()
        if not cleaned:
            cleaned = f"col_{i}"
            stats.renamed_columns.append(cleaned)
        names.append(cleaned)
    return names


async def _decode_delimited(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats, delimiter: str
) -> AsyncIterator[RowBatch]:
    collector = _BadRowCollector(opts.error_row_limit, stats)
    columns: list[str] | None = None
    pending: list[list[Any]] = []
    row_number = 0
    async for lines in _lines(chunks):
        lines = [ln for ln in lines if ln.strip()]
        if not lines:
            continue
        if columns is None:
            header, lines = lines[0], lines[1:]
            columns = _header_names(next(csv.reader([header], delimiter=delimiter)), stats)
            if not lines:
                continue
        for parsed, raw in zip(csv.reader(lines, delimiter=delimiter), lines, strict=False):
            row_number += 1
            if len(parsed) != len(columns):
                collector.add(
                    row_number, raw, f"expected {len(columns)} columns, got {len(parsed)}"
                )
                continue
            stats.rows_ok += 1
            pending.append(parsed)
            if len(pending) >= opts.batch_size:
                yield RowBatch(columns=columns, rows=pending)
                pending = []
    if columns is not None and pending:
        yield RowBatch(columns=columns, rows=pending)


def _dict_row(obj: dict[str, Any], columns: list[str]) -> list[Any]:
    return [obj.get(c) for c in columns]


async def _decode_jsonl(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats
) -> AsyncIterator[RowBatch]:
    collector = _BadRowCollector(opts.error_row_limit, stats)
    columns: list[str] | None = None
    pending: list[list[Any]] = []
    row_number = 0
    async for lines in _lines(chunks):
        for raw in lines:
            if not raw.strip():
                continue
            row_number += 1
            try:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError("line is not a JSON object")
            except ValueError as exc:
                collector.add(row_number, raw, str(exc))
                continue
            if columns is None:
                columns = list(obj.keys())
            stats.rows_ok += 1
            pending.append(_dict_row(obj, columns))
            if len(pending) >= opts.batch_size:
                yield RowBatch(columns=columns, rows=pending)
                pending = []
    if columns is not None and pending:
        yield RowBatch(columns=columns, rows=pending)


async def _decode_json_array(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats
) -> AsyncIterator[RowBatch]:
    """Incrementally parse a top-level JSON array without buffering the file."""
    collector = _BadRowCollector(opts.error_row_limit, stats)
    decoder = json.JSONDecoder()
    buffer = ""
    started = done = False
    columns: list[str] | None = None
    pending: list[list[Any]] = []
    row_number = 0

    def _drain() -> list[RowBatch]:
        nonlocal buffer, started, done, columns, pending, row_number
        out: list[RowBatch] = []
        while True:
            buffer = buffer.lstrip()
            if not buffer or done:
                return out
            if not started:
                if buffer[0] != "[":
                    raise PermanentJobError(
                        ErrorCategory.DECODE_ERROR, "json format requires a top-level array"
                    )
                started = True
                buffer = buffer[1:]
                continue
            if buffer[0] == ",":
                buffer = buffer[1:]
                continue
            if buffer[0] == "]":
                done = True
                buffer = buffer[1:]
                return out
            try:
                obj, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                return out  # need more bytes
            raw_text = buffer[:end]
            buffer = buffer[end:]
            row_number += 1
            if not isinstance(obj, dict):
                collector.add(row_number, raw_text, "array element is not a JSON object")
                continue
            if columns is None:
                columns = list(obj.keys())
            stats.rows_ok += 1
            pending.append(_dict_row(obj, columns))
            if len(pending) >= opts.batch_size:
                out.append(RowBatch(columns=columns, rows=pending))
                pending = []

    async for chunk in chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        for batch in _drain():
            yield batch
    for batch in _drain():
        yield batch
    if started and not done:
        raise PermanentJobError(ErrorCategory.DECODE_ERROR, "truncated JSON array")
    if columns is not None and pending:
        yield RowBatch(columns=columns, rows=pending)


async def _spool_to_tempfile(chunks: AsyncIterator[bytes]) -> Path:
    """Spool the stream to disk (bounded memory) for formats needing a file."""
    with tempfile.NamedTemporaryFile(prefix="wr-ingest-", delete=False) as fh:
        async for chunk in chunks:
            fh.write(chunk)
        return Path(fh.name)


async def _decode_parquet(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats
) -> AsyncIterator[RowBatch]:
    import pyarrow.parquet as pq

    path = await _spool_to_tempfile(chunks)
    try:
        try:
            parquet = pq.ParquetFile(path)
        except Exception as exc:
            raise PermanentJobError(
                ErrorCategory.DECODE_ERROR, f"invalid parquet file: {exc}"
            ) from exc
        columns = [f.name for f in parquet.schema_arrow]
        for record_batch in parquet.iter_batches(batch_size=opts.batch_size):
            data = record_batch.to_pydict()
            rows = [[data[c][i] for c in columns] for i in range(record_batch.num_rows)]
            stats.rows_ok += len(rows)
            if rows:
                yield RowBatch(columns=columns, rows=rows)
    finally:
        path.unlink(missing_ok=True)


async def _decode_avro(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats
) -> AsyncIterator[RowBatch]:
    import fastavro

    collector = _BadRowCollector(opts.error_row_limit, stats)
    path = await _spool_to_tempfile(chunks)
    try:
        columns: list[str] | None = None
        pending: list[list[Any]] = []
        row_number = 0
        with open(path, "rb") as fh:
            try:
                reader = fastavro.reader(fh)
                for obj in reader:
                    row_number += 1
                    if not isinstance(obj, dict):
                        collector.add(row_number, obj, "avro record is not a mapping")
                        continue
                    if columns is None:
                        columns = list(obj.keys())
                    stats.rows_ok += 1
                    pending.append(_dict_row(obj, columns))
                    if len(pending) >= opts.batch_size:
                        yield RowBatch(columns=columns, rows=pending)
                        pending = []
            except (ValueError, EOFError) as exc:
                raise PermanentJobError(
                    ErrorCategory.DECODE_ERROR, f"invalid avro container: {exc}"
                ) from exc
        if columns is not None and pending:
            yield RowBatch(columns=columns, rows=pending)
    finally:
        path.unlink(missing_ok=True)


def _xml_localname(tag: str) -> str:
    """Strip a leading ``{namespace}`` so columns read as plain tag/attr names."""
    if tag and tag[0] == "{":
        return tag.rsplit("}", 1)[1]
    return tag


def _flatten_xml_record(elem: ET.Element) -> dict[str, str]:
    """Flatten one record element into a flat {column: text} map.

    Attributes become columns under their (namespace-stripped) name; nested child
    elements are flattened with ``_``-joined paths; leaf text is the value. First
    write wins on a collision. Bronze is string-typed, so every value is a string.
    """
    out: dict[str, str] = {}

    def walk(node: ET.Element, path: str) -> None:
        for ak, av in node.attrib.items():
            col = f"{path}_{_xml_localname(ak)}" if path else _xml_localname(ak)
            out.setdefault(col, av)
        kids = list(node)
        if not kids:
            if path:
                text = (node.text or "").strip()
                out.setdefault(path, text)
            return
        for kid in kids:
            child_path = f"{path}_{_xml_localname(kid.tag)}" if path else _xml_localname(kid.tag)
            walk(kid, child_path)

    walk(elem, "")
    return out


class _DtdRejected(Exception):
    """A DOCTYPE was seen in the XML prolog (entity-expansion DoS vector)."""


class _PrologDone(Exception):
    """The root element started with no DOCTYPE — the prolog is clean."""


def _reject_dtd(path: Path) -> None:
    """Reject an uploaded XML document that declares a DTD/DOCTYPE, defeating the
    internal entity-expansion ("billion laughs") DoS without adding a dependency.

    Internal entities (the expansion vector) can only be defined inside a
    DOCTYPE, and a DOCTYPE is only legal in the prolog, before the root element.
    So a single expat pass that bails at the first DOCTYPE (reject) or the first
    element start (clean) is sufficient — it never parses document content, and
    stdlib expat never resolves EXTERNAL entities."""
    p = expat.ParserCreate()

    def _on_doctype(*_a):
        raise _DtdRejected()

    def _on_start(*_a):
        raise _PrologDone()

    p.StartDoctypeDeclHandler = _on_doctype
    p.StartElementHandler = _on_start
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            final = not chunk
            try:
                p.Parse(chunk, final)
            except _DtdRejected:
                raise PermanentJobError(
                    ErrorCategory.DECODE_ERROR,
                    "XML DTDs/entity declarations are not allowed",
                ) from None
            except _PrologDone:
                return  # reached the root element, prolog is clean
            if final:
                return


async def _decode_xml(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats
) -> AsyncIterator[RowBatch]:
    """Stream-decode XML, treating each direct child of the root as a record.

    e.g. ``<claims><claim id="1"><type>auto</type></claim>...</claims>`` yields one
    row per ``<claim>`` with columns ``id``, ``type``. Uses iterparse + per-record
    ``clear()`` so memory stays bounded regardless of file size.
    """
    collector = _BadRowCollector(opts.error_row_limit, stats)
    path = await _spool_to_tempfile(chunks)
    try:
        # Guard against internal entity-expansion (billion-laughs) BEFORE the
        # streaming parse: reject any document that declares a DTD/DOCTYPE.
        _reject_dtd(path)
        columns: list[str] | None = None
        pending: list[list[Any]] = []
        row_number = 0
        depth = 0
        root: ET.Element | None = None
        try:
            for event, elem in ET.iterparse(str(path), events=("start", "end")):
                if event == "start":
                    if root is None:
                        root = elem
                    depth += 1
                    continue
                depth -= 1
                if depth != 1:  # only direct children of the root are records
                    if depth == 0 and root is not None:
                        root.clear()
                    continue
                row_number += 1
                try:
                    record = _flatten_xml_record(elem)
                finally:
                    elem.clear()
                    if root is not None:
                        root.clear()  # drop processed shells -> bounded memory
                if not record:
                    collector.add(row_number, _xml_localname(elem.tag), "empty xml record")
                    continue
                if columns is None:
                    columns = list(record.keys())
                stats.rows_ok += 1
                pending.append(_dict_row(record, columns))
                if len(pending) >= opts.batch_size:
                    yield RowBatch(columns=columns, rows=pending)
                    pending = []
        except ET.ParseError as exc:
            raise PermanentJobError(
                ErrorCategory.DECODE_ERROR, f"invalid xml document: {exc}"
            ) from exc
        if columns is not None and pending:
            yield RowBatch(columns=columns, rows=pending)
    finally:
        path.unlink(missing_ok=True)


def decode_stream(
    chunks: AsyncIterator[bytes], opts: DecodeOptions, stats: DecodeStats
) -> AsyncIterator[RowBatch]:
    """Dispatch to the format decoder. Raises PermanentJobError on fatal decode issues."""
    if opts.file_format == "csv":
        return _decode_delimited(chunks, opts, stats, ",")
    if opts.file_format == "tsv":
        return _decode_delimited(chunks, opts, stats, "\t")
    if opts.file_format == "jsonl":
        return _decode_jsonl(chunks, opts, stats)
    if opts.file_format == "json":
        return _decode_json_array(chunks, opts, stats)
    if opts.file_format == "parquet":
        return _decode_parquet(chunks, opts, stats)
    if opts.file_format == "avro":
        return _decode_avro(chunks, opts, stats)
    if opts.file_format == "xml":
        return _decode_xml(chunks, opts, stats)
    raise PermanentJobError(
        ErrorCategory.DECODE_ERROR, f"unsupported file_format {opts.file_format!r}"
    )

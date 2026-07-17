"""Chunk assembly: duplicate / out-of-order / missing parts, size caps,
checksums (ING-FR-040/042, BR-8)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.api.schemas import (
    IngestionCreate,
    NewDataset,
    PartManifestEntry,
    UploadComplete,
    UploadCreate,
)
from app.domain.errors import (
    ChecksumMismatchError,
    ConflictError,
    PayloadTooLargeError,
    UploadExpiredError,
    ValidationFailedError,
)
from app.domain.services.ingestions import IngestionService
from app.domain.services.uploads import UploadService
from app.store.models import Upload
from tests.util import csv_blob, slice_parts

PART_SIZE = 512


async def _stream(data: bytes, chunk: int = 128):
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


async def make_upload(container, principal, *, allow_empty: bool = False):
    _status, job = await IngestionService(container).create(
        principal,
        IngestionCreate(
            ingestion_mode="file_upload",
            file_format="csv",
            new_dataset=NewDataset(name="chunks"),
            allow_empty=allow_empty,
        ),
    )
    upload = await UploadService(container).create(
        principal, UploadCreate(ingestion_id=job["id"], part_size=PART_SIZE)
    )
    return job, upload


def manifest_of(parts: list[dict]) -> UploadComplete:
    return UploadComplete(
        parts=[PartManifestEntry(n=p["n"], etag=p["etag"], size=p["size"]) for p in parts]
    )


async def test_out_of_order_parts_assemble_correctly(container, principal_a) -> None:
    blob = csv_blob(60)
    parts = slice_parts(blob, PART_SIZE)
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    results: dict[int, dict] = {}
    order = list(range(len(parts), 0, -1))  # fully reversed
    for n in order:
        results[n] = await svc.put_part(principal_a, up["upload_id"], n, _stream(parts[n - 1]))
    state = await svc.get(principal_a, up["upload_id"])
    assert [p["n"] for p in state["parts"]] == list(range(1, len(parts) + 1))
    status, job = await svc.complete(
        principal_a, up["upload_id"], manifest_of(sorted(results.values(), key=lambda p: p["n"]))
    )
    assert status == 202
    assert job["status"] == "completed"
    assert job["rows_appended"] == 60  # ordering preserved despite reversed upload


async def test_duplicate_part_same_bytes_is_idempotent(container, principal_a) -> None:
    blob = csv_blob(40)
    parts = slice_parts(blob, PART_SIZE)
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    first = await svc.put_part(principal_a, up["upload_id"], 1, _stream(parts[0]))
    again = await svc.put_part(principal_a, up["upload_id"], 1, _stream(parts[0]))
    assert first == again


async def test_duplicate_part_different_bytes_conflicts(container, principal_a) -> None:
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    await svc.put_part(principal_a, up["upload_id"], 1, _stream(b"a" * PART_SIZE))
    with pytest.raises(ConflictError) as exc:
        await svc.put_part(principal_a, up["upload_id"], 1, _stream(b"b" * PART_SIZE))
    assert "different etag" in exc.value.message


async def test_missing_parts_block_complete(container, principal_a) -> None:
    blob = csv_blob(150)
    parts = slice_parts(blob, PART_SIZE)
    assert len(parts) >= 3
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    uploaded = []
    for n in (1, 3):  # skip part 2
        uploaded.append(await svc.put_part(principal_a, up["upload_id"], n, _stream(parts[n - 1])))
    with pytest.raises(ConflictError) as exc:
        await svc.complete(principal_a, up["upload_id"], manifest_of(uploaded))
    assert exc.value.details["missing_parts"] == [2]


async def test_manifest_must_cover_all_uploaded_parts(container, principal_a) -> None:
    blob = csv_blob(150)
    parts = slice_parts(blob, PART_SIZE)
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    uploaded = [
        await svc.put_part(principal_a, up["upload_id"], n, _stream(parts[n - 1]))
        for n in range(1, len(parts) + 1)
    ]
    with pytest.raises(ConflictError) as exc:
        await svc.complete(principal_a, up["upload_id"], manifest_of(uploaded[:-1]))
    assert exc.value.details["unlisted_parts"] == [len(parts)]


async def test_non_last_part_must_equal_part_size(container, principal_a) -> None:
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    short = await svc.put_part(principal_a, up["upload_id"], 1, _stream(b"h,i\n1,2\n"))
    full = await svc.put_part(principal_a, up["upload_id"], 2, _stream(b"x" * PART_SIZE))
    with pytest.raises(ConflictError) as exc:
        await svc.complete(principal_a, up["upload_id"], manifest_of([short, full]))
    assert any(p.get("expected_size") == PART_SIZE for p in exc.value.details["parts"])


async def test_oversize_part_rejected(container, principal_a) -> None:
    """Max chunk size is enforced while streaming — no buffering of the excess."""
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    with pytest.raises(PayloadTooLargeError):
        await svc.put_part(principal_a, up["upload_id"], 1, _stream(b"x" * (PART_SIZE + 1)))
    state = await svc.get(principal_a, up["upload_id"])
    assert state["parts"] == []  # nothing confirmed


async def test_part_content_hash_verification(container, principal_a) -> None:
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    data = b"z" * PART_SIZE
    good = hashlib.sha256(data).hexdigest()
    with pytest.raises(ChecksumMismatchError):
        await svc.put_part(principal_a, up["upload_id"], 1, _stream(data), content_sha256="0" * 64)
    result = await svc.put_part(principal_a, up["upload_id"], 1, _stream(data), content_sha256=good)
    assert result["etag"] == good


async def test_whole_file_sha256_verified_on_complete(container, principal_a) -> None:
    blob = csv_blob(60)
    parts = slice_parts(blob, PART_SIZE)
    _job, up = await make_upload(container, principal_a)
    svc = UploadService(container)
    uploaded = [
        await svc.put_part(principal_a, up["upload_id"], n, _stream(parts[n - 1]))
        for n in range(1, len(parts) + 1)
    ]
    bad = manifest_of(uploaded)
    bad_body = UploadComplete(parts=bad.parts, sha256="f" * 64)
    with pytest.raises(ChecksumMismatchError):
        await svc.complete(principal_a, up["upload_id"], bad_body)
    good_body = UploadComplete(parts=bad.parts, sha256=hashlib.sha256(blob).hexdigest())
    status, job = await svc.complete(principal_a, up["upload_id"], good_body)
    assert status == 202 and job["status"] == "completed"


async def test_expired_upload_returns_410_and_expires_job(container, principal_a) -> None:
    _job, up = await make_upload(container, principal_a)
    async with container.db.tenant_session(principal_a.tenant_id) as session:
        await session.execute(
            sa.update(Upload)
            .where(Upload.id == up["upload_id"])
            .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()
    svc = UploadService(container)
    with pytest.raises(UploadExpiredError):
        await svc.put_part(principal_a, up["upload_id"], 1, _stream(b"x" * PART_SIZE))
    state = await svc.get(principal_a, up["upload_id"])
    assert state["status"] == "expired"
    job = await IngestionService(container).get(principal_a, _job["id"])
    assert job["status"] == "expired"


async def test_invalid_part_size_rejected(container, principal_a) -> None:
    _status, job = await IngestionService(container).create(
        principal_a,
        IngestionCreate(
            ingestion_mode="file_upload", file_format="csv", new_dataset=NewDataset(name="x")
        ),
    )
    with pytest.raises(ValidationFailedError):
        await UploadService(container).create(
            principal_a, UploadCreate(ingestion_id=job["id"], part_size=1)
        )

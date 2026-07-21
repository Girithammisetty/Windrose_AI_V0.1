"""Chunked resumable upload path (ING-FR-040..044, BR-8, AC-4/5).

Parts stream straight into the ObjectStore — request bodies are consumed as
async byte iterators with a hard per-part size cap, so a whole file is never
buffered in memory (ING-FR-041). Part state lives in Postgres, bytes in the
object store, so uploads survive restarts (ING-FR-042).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal
from app.api.schemas import UploadComplete, UploadCreate
from app.container import Container
from app.domain.errors import (
    ChecksumMismatchError,
    ConflictError,
    PayloadTooLargeError,
    RateLimitedError,
    UploadExpiredError,
    ValidationFailedError,
)
from app.domain.services.common import iso, raise_not_found_with_audit
from app.domain.services.transitions import record_transition
from app.domain.state_machine import TransitionContext
from app.ids import uuid7
from app.store.models import Ingestion, Upload, UploadPart


def enforce_upload_caps(total_size: int, part_count: int, settings: Any) -> None:
    """B2 (BRD 58): reject an assembled upload that exceeds the configured total
    size / part-count caps, before it reaches the memory-bound Iceberg commit.
    A cap of 0 means unlimited. Raises ValidationFailedError (HTTP 400)."""
    max_parts = getattr(settings, "max_upload_parts", 0)
    max_bytes = getattr(settings, "max_upload_bytes", 0)
    if max_parts and part_count > max_parts:
        raise ValidationFailedError(
            f"upload has {part_count} parts, exceeding the limit of {max_parts}",
            details=[{"field": "parts", "message": "too many parts"}],
        )
    if max_bytes and total_size > max_bytes:
        raise ValidationFailedError(
            f"upload total {total_size} bytes exceeds the {max_bytes}-byte limit",
            details=[{"field": "size", "message": "file too large"}],
        )


def serialize_upload(upload: Upload, parts: list[UploadPart]) -> dict[str, Any]:
    return {
        "upload_id": upload.id,
        "ingestion_id": upload.ingestion_id,
        "status": upload.status,
        "part_size": upload.part_size,
        "bytes_total": upload.bytes_total,
        "sha256": upload.sha256,
        "expires_at": iso(upload.expires_at),
        "parts": [{"n": p.n, "etag": p.etag, "size": p.size} for p in parts],
    }


class UploadService:
    def __init__(self, container: Container) -> None:
        self.c = container

    async def _get_upload(
        self, session: AsyncSession, principal: Principal, upload_id: str
    ) -> Upload:
        upload = (
            await session.execute(
                sa.select(Upload).where(
                    Upload.id == upload_id, Upload.tenant_id == principal.tenant_id
                )
            )
        ).scalar_one_or_none()
        if upload is None:
            await raise_not_found_with_audit(session, principal, Upload, upload_id, "upload")
        return upload

    async def _parts(self, session: AsyncSession, upload_id: str) -> list[UploadPart]:
        rows = (
            await session.execute(
                sa.select(UploadPart)
                .where(UploadPart.upload_id == upload_id)
                .order_by(UploadPart.n)
            )
        ).scalars()
        return list(rows.all())

    async def _ensure_open(self, session: AsyncSession, upload: Upload) -> None:
        """ING-FR-044: expire on 24h inactivity; abort cloud multipart (stub-side no-op)."""
        if upload.status == "expired":
            raise UploadExpiredError("upload expired")
        if upload.status != "open":
            raise ConflictError(f"upload is {upload.status}", details={"status": upload.status})
        if datetime.now(UTC) >= _aware(upload.expires_at):
            upload.status = "expired"
            ing = (
                await session.execute(
                    sa.select(Ingestion).where(Ingestion.id == upload.ingestion_id)
                )
            ).scalar_one_or_none()
            if ing is not None and ing.status == "awaiting_upload":
                record_transition(
                    session,
                    ing,
                    "expired",
                    TransitionContext(),
                    event_payload={"ingestion_id": ing.id, "upload_id": upload.id},
                )
            await session.commit()
            raise UploadExpiredError("upload expired")

    # ------------------------------------------------------------------ init
    async def create(self, principal: Principal, body: UploadCreate) -> dict[str, Any]:
        s = self.c.settings
        part_size = body.part_size if body.part_size is not None else s.default_part_size
        if not (s.min_part_size <= part_size <= s.max_part_size):
            raise ValidationFailedError(
                "invalid part_size",
                details=[
                    {
                        "field": "part_size",
                        "message": f"must be between {s.min_part_size} and {s.max_part_size} bytes",
                    }
                ],
            )
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            ing = (
                await session.execute(
                    sa.select(Ingestion).where(
                        Ingestion.id == body.ingestion_id,
                        Ingestion.tenant_id == principal.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if ing is None:
                await raise_not_found_with_audit(
                    session, principal, Ingestion, body.ingestion_id, "ingestion"
                )
            if ing.ingestion_mode != "file_upload":
                raise ValidationFailedError(
                    "uploads require a file_upload ingestion",
                    details=[{"field": "ingestion_id", "message": "job is not file_upload mode"}],
                )
            if ing.status not in ("created", "awaiting_upload"):
                raise ConflictError(
                    "ingestion not awaiting upload", details={"current_status": ing.status}
                )
            active = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Upload)
                    .where(Upload.tenant_id == principal.tenant_id, Upload.status == "open")
                )
            ).scalar_one()
            if active >= s.max_active_uploads_per_tenant:  # ING-FR-082
                raise RateLimitedError(
                    "active upload cap reached",
                    details={"max_active_uploads": s.max_active_uploads_per_tenant},
                )
            upload = Upload(
                id=uuid7(),
                tenant_id=principal.tenant_id,
                ingestion_id=ing.id,
                part_size=part_size,
                storage_prefix=f"tenants/{principal.tenant_id}/uploads/{ing.id}",
                bytes_total=body.bytes_total,
                status="open",
                expires_at=datetime.now(UTC) + timedelta(hours=s.upload_ttl_hours),
            )
            session.add(upload)
            if ing.status == "created":
                record_transition(
                    session,
                    ing,
                    "awaiting_upload",
                    TransitionContext(ingestion_mode="file_upload", upload_session_opened=True),
                    actor=principal.actor(),
                    via_agent=principal.via_agent(),
                )
            if body.bytes_total is not None:
                ing.bytes_total = body.bytes_total
            await session.commit()
            return {
                "upload_id": upload.id,
                "part_size": upload.part_size,
                "expires_at": iso(upload.expires_at),
            }

    # ------------------------------------------------------------------ part
    async def put_part(
        self,
        principal: Principal,
        upload_id: str,
        n: int,
        stream: AsyncIterator[bytes],
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        if n < 1:
            raise ValidationFailedError(
                "invalid part number", details=[{"field": "n", "message": "must be >= 1"}]
            )
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            upload = await self._get_upload(session, principal, upload_id)
            await self._ensure_open(session, upload)
            part_size = upload.part_size
            prefix = upload.storage_prefix

        # stream to a staging key with a hard size cap (max chunk size enforcement)
        staging_key = f"{prefix}/staging/part-{n}-{uuid7()}"

        async def capped() -> AsyncIterator[bytes]:
            total = 0
            async for chunk in stream:
                total += len(chunk)
                if total > part_size:
                    raise PayloadTooLargeError(
                        "part exceeds part_size",
                        details={"part_size": part_size, "n": n},
                    )
                yield chunk

        try:
            result = await self.c.object_store.put(staging_key, capped())
        except PayloadTooLargeError:
            await self.c.object_store.delete(staging_key)
            raise

        if content_sha256 and content_sha256.lower() != result.etag:
            await self.c.object_store.delete(staging_key)
            raise ChecksumMismatchError(
                "part checksum mismatch",
                details={"n": n, "expected": content_sha256.lower(), "computed": result.etag},
            )

        final_key = f"{prefix}/parts/{n:06d}"
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            existing = (
                await session.execute(
                    sa.select(UploadPart).where(
                        UploadPart.upload_id == upload_id, UploadPart.n == n
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                await self.c.object_store.delete(staging_key)
                if existing.etag == result.etag:  # BR-8: idempotent duplicate re-PUT
                    return {"n": existing.n, "etag": existing.etag, "size": existing.size}
                raise ConflictError(
                    "part already uploaded with a different etag",
                    details={"n": n, "existing_etag": existing.etag, "new_etag": result.etag},
                )
            await self.c.object_store.move(staging_key, final_key)
            session.add(
                UploadPart(
                    upload_id=upload_id,
                    n=n,
                    tenant_id=principal.tenant_id,
                    etag=result.etag,
                    size=result.size,
                    storage_key=final_key,
                )
            )
            try:
                await session.flush()
            except IntegrityError:  # lost a concurrent race for the same part
                await session.rollback()
                async with self.c.db.tenant_session(principal.tenant_id) as retry_session:
                    existing = (
                        await retry_session.execute(
                            sa.select(UploadPart).where(
                                UploadPart.upload_id == upload_id, UploadPart.n == n
                            )
                        )
                    ).scalar_one()
                if existing.etag == result.etag:
                    return {"n": existing.n, "etag": existing.etag, "size": existing.size}
                raise ConflictError(
                    "part already uploaded with a different etag",
                    details={"n": n, "existing_etag": existing.etag, "new_etag": result.etag},
                ) from None
            total = (
                await session.execute(
                    sa.select(sa.func.coalesce(sa.func.sum(UploadPart.size), 0)).where(
                        UploadPart.upload_id == upload_id
                    )
                )
            ).scalar_one()
            ing = (
                await session.execute(
                    sa.select(Ingestion).where(Ingestion.id == upload.ingestion_id)
                )
            ).scalar_one_or_none()
            if ing is not None:
                ing.bytes_received = int(total)
            await session.commit()
        return {"n": n, "etag": result.etag, "size": result.size}

    # ------------------------------------------------------------------- get
    async def get(self, principal: Principal, upload_id: str) -> dict[str, Any]:
        """ING-FR-042 / AC-5: confirmed parts for resume."""
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            upload = await self._get_upload(session, principal, upload_id)
            parts = await self._parts(session, upload_id)
            return serialize_upload(upload, parts)

    # -------------------------------------------------------------- complete
    async def complete(
        self, principal: Principal, upload_id: str, body: UploadComplete
    ) -> tuple[int, dict[str, Any]]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            upload = await self._get_upload(session, principal, upload_id)
            await self._ensure_open(session, upload)
            parts = await self._parts(session, upload_id)
            stored = {p.n: p for p in parts}

            manifest_ns = [p.n for p in body.parts]
            if len(set(manifest_ns)) != len(manifest_ns):
                raise ValidationFailedError(
                    "duplicate part numbers in manifest",
                    details=[{"field": "parts", "message": "part numbers must be unique"}],
                )
            missing = sorted(set(manifest_ns) - set(stored))
            if missing:
                raise ConflictError(
                    "manifest lists parts not uploaded", details={"missing_parts": missing}
                )
            unlisted = sorted(set(stored) - set(manifest_ns))
            if unlisted:
                raise ConflictError(
                    "uploaded parts missing from manifest", details={"unlisted_parts": unlisted}
                )
            expected_ns = list(range(1, len(manifest_ns) + 1))
            if sorted(manifest_ns) != expected_ns:
                raise ConflictError(
                    "parts are not contiguous from 1",
                    details={"missing_parts": sorted(set(expected_ns) - set(manifest_ns))},
                )
            mismatches = []
            last_n = max(manifest_ns)
            for entry in body.parts:
                part = stored[entry.n]
                if part.etag != entry.etag or part.size != entry.size:
                    mismatches.append({"n": entry.n, "etag": part.etag, "size": part.size})
                elif entry.n != last_n and part.size != upload.part_size:  # BR-8
                    mismatches.append(
                        {"n": entry.n, "size": part.size, "expected_size": upload.part_size}
                    )
            if mismatches:
                raise ConflictError("part manifest mismatch", details={"parts": mismatches})

            total_size = sum(p.size for p in parts)

            # B2 (BRD 58): hard cap total size + part count BEFORE the memory-bound
            # commit path, so an oversized upload fails fast instead of OOMing.
            enforce_upload_caps(total_size, len(parts), self.c.settings)

            part_keys = [stored[n].storage_key for n in expected_ns]

        if body.sha256:  # streamed whole-file verification (ING-FR-043 step 1)
            digest = hashlib.sha256()
            for key in part_keys:
                async for chunk in self.c.object_store.open_stream(key):
                    digest.update(chunk)
            if digest.hexdigest() != body.sha256.lower():
                raise ChecksumMismatchError(
                    "file sha256 mismatch",
                    details={"expected": body.sha256.lower(), "computed": digest.hexdigest()},
                )

        async with self.c.db.tenant_session(principal.tenant_id) as session:
            upload = await self._get_upload(session, principal, upload_id)
            upload.status = "completed"
            upload.sha256 = body.sha256.lower() if body.sha256 else None
            ing = (
                await session.execute(
                    sa.select(Ingestion).where(Ingestion.id == upload.ingestion_id)
                )
            ).scalar_one()
            ing.bytes_total = total_size
            ing.bytes_received = total_size
            record_transition(
                session,
                ing,
                "queued",
                TransitionContext(payload_valid=True),
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            await session.commit()
            ingestion_id = ing.id

        if self.c.settings.inline_execution:
            from app.domain.services.runner import IngestionRunner

            await IngestionRunner(self.c).execute(principal.tenant_id, ingestion_id)

        async with self.c.db.tenant_session(principal.tenant_id) as session:
            from app.domain.services.ingestions import serialize_ingestion

            ing = (
                await session.execute(sa.select(Ingestion).where(Ingestion.id == ingestion_id))
            ).scalar_one()
            return 202, serialize_ingestion(ing)

    # ----------------------------------------------------------------- abort
    async def abort(self, principal: Principal, upload_id: str) -> None:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            upload = await self._get_upload(session, principal, upload_id)
            if upload.status not in ("open", "expired"):
                raise ConflictError(f"upload is {upload.status}")
            upload.status = "aborted"
            await session.commit()
            prefix = upload.storage_prefix
        await self.c.object_store.delete_prefix(prefix)

    # -------------------------------------------------------------------- gc
    async def gc_expired(self, tenant_id: str) -> int:
        """ING-FR-044 + BR-14: expire stale uploads, GC orphaned part files."""
        expired = 0
        async with self.c.db.tenant_session(tenant_id) as session:
            uploads = (
                (
                    await session.execute(
                        sa.select(Upload).where(
                            Upload.tenant_id == tenant_id,
                            Upload.status == "open",
                            Upload.expires_at <= datetime.now(UTC),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for upload in uploads:
                upload.status = "expired"
                ing = (
                    await session.execute(
                        sa.select(Ingestion).where(Ingestion.id == upload.ingestion_id)
                    )
                ).scalar_one_or_none()
                if ing is not None and ing.status == "awaiting_upload":
                    record_transition(
                        session,
                        ing,
                        "expired",
                        TransitionContext(),
                        event_payload={"ingestion_id": ing.id, "upload_id": upload.id},
                    )
                expired += 1
            await session.commit()
            prefixes = [u.storage_prefix for u in uploads]
        for prefix in prefixes:
            await self.c.object_store.delete_prefix(prefix)
        return expired


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat stored values as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

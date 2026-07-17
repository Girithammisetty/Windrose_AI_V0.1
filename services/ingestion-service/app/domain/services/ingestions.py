"""Ingestion job lifecycle (ING-FR-020..028, BR-2/13)."""

from __future__ import annotations

import re
import secrets as pysecrets
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal
from app.api.schemas import DEFAULT_WORKSPACE, IngestionCreate
from app.container import Container
from app.domain.decode import FILE_FORMATS
from app.domain.errors import (
    ConflictError,
    NotFoundError,
    NotImplementedFeatureError,
    ValidationFailedError,
)
from app.domain.secrets import webhook_secret_path
from app.domain.services.common import iso, raise_not_found_with_audit
from app.domain.services.transitions import ingestion_urn, record_transition
from app.domain.state_machine import TERMINAL_STATUSES, TransitionContext
from app.events.outbox import emit_event
from app.ids import uuid7
from app.store.models import Connection, Ingestion, UploadPart, WebhookEndpoint

_DATASET_URN_RE = re.compile(r"^wr:([0-9a-fA-F-]{36}):dataset:dataset/([0-9a-fA-F-]{36})$")


def parse_dataset_urn(urn: str) -> tuple[str, str]:
    match = _DATASET_URN_RE.match(urn)
    if not match:
        raise ValidationFailedError(
            "invalid dataset_urn",
            details=[
                {"field": "dataset_urn", "message": "expected wr:<tenant>:dataset:dataset/<id>"}
            ],
        )
    return match.group(1).lower(), match.group(2).lower()


def bronze_table_ident(tenant_id: str, dataset_urn: str) -> str:
    """BR-13 / ING-FR-043: bronze.<tenant_id>.ds_<dataset_id>."""
    _tenant, dataset_id = parse_dataset_urn(dataset_urn)
    return f"bronze.{tenant_id}.ds_{dataset_id}"


def serialize_ingestion(ing: Ingestion) -> dict[str, Any]:
    return {
        "id": ing.id,
        "ingestion_mode": ing.ingestion_mode,
        "status": ing.status,
        "trigger": ing.trigger,
        "connection_id": ing.connection_id,
        "dataset_urn": ing.dataset_urn,
        "new_dataset": ing.new_dataset,
        "file_format": ing.file_format,
        "statement": ing.statement,
        "schedule_id": ing.schedule_id,
        "scheduled_for": iso(ing.scheduled_for),
        "skip_profiling": ing.skip_profiling,
        "allow_empty": ing.allow_empty,
        "error_row_limit": ing.error_row_limit,
        "bytes_total": ing.bytes_total,
        "bytes_received": ing.bytes_received,
        "rows_appended": ing.rows_appended,
        "iceberg_snapshot_id": ing.iceberg_snapshot_id,
        "attempts": ing.attempts,
        "retried_from_id": ing.retried_from_id,
        "error_log": ing.error_log,
        "workspace_id": ing.workspace_id,
        "started_at": iso(ing.started_at),
        "finished_at": iso(ing.finished_at),
        "created_at": iso(ing.created_at),
        "updated_at": iso(ing.updated_at),
    }


class IngestionService:
    def __init__(self, container: Container) -> None:
        self.c = container

    async def _get(
        self, session: AsyncSession, principal: Principal, ingestion_id: str
    ) -> Ingestion:
        ing = (
            await session.execute(
                sa.select(Ingestion).where(
                    Ingestion.id == ingestion_id, Ingestion.tenant_id == principal.tenant_id
                )
            )
        ).scalar_one_or_none()
        if ing is None:
            await raise_not_found_with_audit(
                session, principal, Ingestion, ingestion_id, "ingestion"
            )
        return ing

    async def _resolve_target(
        self, session: AsyncSession, principal: Principal, body: IngestionCreate
    ) -> tuple[str, dict[str, Any] | None]:
        if bool(body.dataset_urn) == bool(body.new_dataset):
            raise ValidationFailedError(
                "exactly one target required",
                details=[
                    {"field": "dataset_urn", "message": "provide dataset_urn XOR new_dataset"},
                ],
            )
        if body.dataset_urn:
            urn_tenant, _ = parse_dataset_urn(body.dataset_urn)
            if urn_tenant != principal.tenant_id:
                # BR-13 / MASTER-FR-003: cross-tenant URN -> 404 + audit
                emit_event(
                    session,
                    tenant_id=principal.tenant_id,
                    event_type="security.cross_tenant_denied",
                    resource_urn=body.dataset_urn,
                    payload={"resource_type": "dataset", "resource_urn": body.dataset_urn},
                    actor=principal.actor(),
                    via_agent=principal.via_agent(),
                )
                await session.commit()
                raise NotFoundError()
            return body.dataset_urn, None
        dataset_id = uuid7()
        urn = f"wr:{principal.tenant_id}:dataset:dataset/{dataset_id}"
        return urn, body.new_dataset.model_dump() if body.new_dataset else None

    def _validate_mode(self, body: IngestionCreate) -> None:
        details = []
        if body.ingestion_mode == "webhook_batch":
            # HONEST 501 (ING-FR-024): webhook events would buffer to the object
            # store but the buffer->Iceberg flush is not implemented, so accepted
            # events would NEVER land in a dataset. Reject at request time
            # instead of accepting work that silently never happens.
            raise NotImplementedFeatureError(
                "webhook_batch ingestion is not available in this deployment: "
                "the buffered-events flush to Iceberg is not implemented "
                "(TODO wave-2 Temporal timer workflow); accepted events would "
                "never become dataset rows"
            )
        if body.ingestion_mode == "file_upload":
            if not body.file_format:
                details.append(
                    {"field": "file_format", "message": "required for file_upload (BR-2)"}
                )
            elif body.file_format not in FILE_FORMATS:
                details.append(
                    {"field": "file_format", "message": f"must be one of {list(FILE_FORMATS)}"}
                )
        elif body.ingestion_mode == "query":
            if not body.statement:
                details.append({"field": "statement", "message": "required for query mode"})
            if not body.connection_id:
                details.append({"field": "connection_id", "message": "required for query mode"})
        elif body.ingestion_mode == "scheduled_run":
            details.append(
                {
                    "field": "ingestion_mode",
                    "message": "scheduled_run jobs are created by schedule fires, not the API",
                }
            )
        if details:
            raise ValidationFailedError("invalid ingestion payload", details=details)

    async def create(
        self, principal: Principal, body: IngestionCreate
    ) -> tuple[int, dict[str, Any]]:
        """Returns (http_status, response body). 202 for query mode (runs async)."""
        self._validate_mode(body)
        webhook_info: dict[str, Any] | None = None
        pending_webhook_secret: tuple[str, dict[str, str]] | None = None
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            if body.connection_id:
                conn = (
                    await session.execute(
                        sa.select(Connection).where(
                            Connection.id == body.connection_id,
                            Connection.tenant_id == principal.tenant_id,
                            Connection.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if conn is None:
                    await raise_not_found_with_audit(
                        session, principal, Connection, body.connection_id, "connection"
                    )
            dataset_urn, new_dataset = await self._resolve_target(session, principal, body)

            # When the caller doesn't pin a workspace (the file-upload path sends
            # none), inherit the caller's JWT workspace so the resulting dataset
            # lands in their workspace and is visible to workspace-scoped reads —
            # otherwise it falls to the NIL workspace and the owner can't see it.
            workspace_id = body.workspace_id
            if workspace_id == DEFAULT_WORKSPACE and principal.workspace_id:
                workspace_id = principal.workspace_id

            ing = Ingestion(
                id=uuid7(),
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                connection_id=body.connection_id,
                dataset_urn=dataset_urn,
                new_dataset=new_dataset,
                ingestion_mode=body.ingestion_mode,
                file_format=body.file_format
                or ("parquet" if body.ingestion_mode == "query" else None),
                statement=body.statement,
                status="created",
                trigger="agent" if principal.typ.startswith("agent") else "manual",
                skip_profiling=body.skip_profiling,
                allow_empty=body.allow_empty,
                error_row_limit=body.error_row_limit,
                created_by=principal.sub,
            )
            session.add(ing)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="ingestion.created",
                resource_urn=ingestion_urn(ing),
                payload={"ingestion_id": ing.id, "ingestion_mode": ing.ingestion_mode},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            if body.ingestion_mode == "webhook_batch":
                webhook_info, pending_webhook_secret = self._create_webhook_endpoint(
                    session, principal, ing
                )
            if body.ingestion_mode in ("query", "webhook_batch"):
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

        # F5: persist the webhook signing secret only after the row is durably
        # committed, so a failed create never leaves an orphaned secret.
        if pending_webhook_secret is not None:
            await self.c.secrets.put(*pending_webhook_secret)

        if body.ingestion_mode == "query" and self.c.settings.inline_execution:
            from app.domain.services.runner import IngestionRunner

            await IngestionRunner(self.c).execute(principal.tenant_id, ingestion_id)

        async with self.c.db.tenant_session(principal.tenant_id) as session:
            ing = await self._get(session, principal, ingestion_id)
            body_out = serialize_ingestion(ing)
            if webhook_info:
                body_out["webhook"] = webhook_info  # signing secret shown exactly once
            status = 202 if body.ingestion_mode == "query" else 201
            body_out["operation_id"] = ingestion_id
            return status, body_out

    def _create_webhook_endpoint(
        self, session: AsyncSession, principal: Principal, ing: Ingestion
    ) -> tuple[dict[str, Any], tuple[str, dict[str, str]]]:
        """ING-FR-024: per-ingestion HMAC signing secret, Vault-stored.

        Adds the endpoint row to the transaction and returns both the caller
        response and the pending secret to persist AFTER commit (F5).
        """
        path_token = f"{principal.tenant_id}.{pysecrets.token_urlsafe(24)}"
        signing_secret = pysecrets.token_hex(32)
        vault_ref = webhook_secret_path(principal.tenant_id, ing.id)
        session.add(
            WebhookEndpoint(
                id=uuid7(),
                tenant_id=principal.tenant_id,
                ingestion_id=ing.id,
                path_token=path_token,
                hmac_vault_ref=vault_ref,
            )
        )
        info = {
            "path_token": path_token,
            "events_url": f"/api/v1/hooks/{path_token}/events",
            "signing_secret": signing_secret,
        }
        return info, (vault_ref, {"signing_secret": signing_secret})

    async def get(self, principal: Principal, ingestion_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            return serialize_ingestion(await self._get(session, principal, ingestion_id))

    async def list(
        self,
        principal: Principal,
        *,
        status: str | None,
        dataset_urn: str | None,
        mode: str | None,
        schedule_id: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from app.api.pagination import paginate

        stmt = sa.select(Ingestion).where(Ingestion.tenant_id == principal.tenant_id)
        if status:
            stmt = stmt.where(Ingestion.status == status)
        if dataset_urn:
            stmt = stmt.where(Ingestion.dataset_urn == dataset_urn)
        if mode:
            stmt = stmt.where(Ingestion.ingestion_mode == mode)
        if schedule_id:
            stmt = stmt.where(Ingestion.schedule_id == schedule_id)
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            items, page = await paginate(session, stmt, Ingestion.id, limit=limit, cursor=cursor)
            return [serialize_ingestion(i) for i in items], page

    async def cancel(self, principal: Principal, ingestion_id: str) -> dict[str, Any]:
        """ING-FR-027: uncommitted only; partial staged data is GC'd."""
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            ing = await self._get(session, principal, ingestion_id)
            record_transition(
                session,
                ing,
                "cancelled",
                TransitionContext(committed=ing.iceberg_snapshot_id is not None),
                actor=principal.actor(),
                via_agent=principal.via_agent(),
                event_payload={"ingestion_id": ing.id},
            )
            ing.finished_at = datetime.now(UTC)
            await session.commit()
            snapshot = serialize_ingestion(ing)
        # BR-14 best effort: orphaned part-files GC
        await self.c.object_store.delete_prefix(
            f"tenants/{principal.tenant_id}/uploads-of/{ingestion_id}"
        )
        return snapshot

    async def retry(self, principal: Principal, ingestion_id: str) -> tuple[int, dict[str, Any]]:
        """ING-FR-081: requeue a failed job as a fresh run (BR-9 prevents duplicates)."""
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            ing = await self._get(session, principal, ingestion_id)
            if ing.status != "failed":
                raise ConflictError(
                    "only failed ingestions can be retried",
                    details={"current_status": ing.status, "requested": "retry"},
                )
            clone = self._clone(ing)
            session.add(clone)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="ingestion.created",
                resource_urn=ingestion_urn(clone),
                payload={"ingestion_id": clone.id, "retried_from": ing.id},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            record_transition(
                session,
                clone,
                "queued",
                TransitionContext(payload_valid=True),
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            await session.commit()
            clone_id = clone.id
        if self.c.settings.inline_execution:
            from app.domain.services.runner import IngestionRunner

            await IngestionRunner(self.c).execute(principal.tenant_id, clone_id)
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            return 202, serialize_ingestion(await self._get(session, principal, clone_id))

    async def reingest(self, principal: Principal, ingestion_id: str) -> tuple[int, dict[str, Any]]:
        """ING-FR-028 (Could): clone config of a terminal job into a new job."""
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            ing = await self._get(session, principal, ingestion_id)
            if ing.status not in TERMINAL_STATUSES:
                raise ConflictError(
                    "only terminal ingestions can be re-ingested",
                    details={"current_status": ing.status, "requested": "reingest"},
                )
            clone = self._clone(ing)
            session.add(clone)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="ingestion.created",
                resource_urn=ingestion_urn(clone),
                payload={"ingestion_id": clone.id, "reingested_from": ing.id},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            record_transition(
                session,
                clone,
                "queued",
                TransitionContext(payload_valid=True),
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            await session.commit()
            clone_id = clone.id
        if self.c.settings.inline_execution and ing.ingestion_mode == "query":
            from app.domain.services.runner import IngestionRunner

            await IngestionRunner(self.c).execute(principal.tenant_id, clone_id)
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            return 202, serialize_ingestion(await self._get(session, principal, clone_id))

    def _clone(self, ing: Ingestion) -> Ingestion:
        return Ingestion(
            id=uuid7(),
            tenant_id=ing.tenant_id,
            workspace_id=ing.workspace_id,
            connection_id=ing.connection_id,
            dataset_urn=ing.dataset_urn,
            new_dataset=ing.new_dataset,
            ingestion_mode=ing.ingestion_mode,
            file_format=ing.file_format,
            statement=ing.statement,
            status="created",
            trigger=ing.trigger,
            schedule_id=ing.schedule_id,
            skip_profiling=ing.skip_profiling,
            allow_empty=ing.allow_empty,
            error_row_limit=ing.error_row_limit,
            retried_from_id=ing.id,
            created_by=ing.created_by,
        )


async def find_upload_parts(session: AsyncSession, ingestion: Ingestion) -> list[UploadPart]:
    """Parts for this job — or, for a retry clone, the original job's parts."""
    from app.store.models import Upload

    for candidate in (ingestion.id, ingestion.retried_from_id):
        if candidate is None:
            continue
        upload = (
            await session.execute(
                sa.select(Upload).where(
                    Upload.ingestion_id == candidate,
                    Upload.tenant_id == ingestion.tenant_id,
                    Upload.status == "completed",
                )
            )
        ).scalar_one_or_none()
        if upload is not None:
            parts = (
                (
                    await session.execute(
                        sa.select(UploadPart)
                        .where(UploadPart.upload_id == upload.id)
                        .order_by(UploadPart.n)
                    )
                )
                .scalars()
                .all()
            )
            return list(parts)
    return []

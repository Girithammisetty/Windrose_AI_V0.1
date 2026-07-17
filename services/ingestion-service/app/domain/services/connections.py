"""Connection lifecycle (ING-FR-001..008, AC-1/2/10)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.api.auth import Principal
from app.api.schemas import ConnectionCreate, ConnectionTestAdhoc, ConnectionUpdate, PreviewRequest
from app.container import Container
from app.domain import connectors
from app.domain.errors import (
    ConflictError,
    ConnectionTestFailedError,
    ErrorCategory,
    NotFoundError,
    RequestTimeoutError,
    ValidationFailedError,
)
from app.domain.errors import UnsupportedConnectorError
from app.domain.probers import ProbeResult, UnsupportedConnectorProber
from app.domain.secrets import connection_secret_path
from app.domain.services.common import iso, raise_not_found_with_audit
from app.domain.state_machine import TERMINAL_STATUSES
from app.events.outbox import emit_event
from app.ids import uuid7
from app.store.models import Connection, Ingestion, Schedule


def connection_urn(conn: Connection) -> str:
    return f"wr:{conn.tenant_id}:ingestion:connection/{conn.id}"


def serialize_connection(conn: Connection) -> dict[str, Any]:
    return {
        "id": conn.id,
        "name": conn.name,
        "connector_type": conn.connector_type,
        "config": conn.config,
        "secrets": connectors.mask_secrets(conn.secret_field_names),
        "secret_set": bool(conn.secret_field_names),
        "traffic_direction": conn.traffic_direction,
        "tags": conn.tags,
        "workspace_id": conn.workspace_id,
        "last_test_status": conn.last_test_status,
        "last_tested_at": iso(conn.last_tested_at),
        "created_at": iso(conn.created_at),
        "updated_at": iso(conn.updated_at),
    }


class ConnectionService:
    def __init__(self, container: Container) -> None:
        self.c = container

    def _ensure_supported(self, connector_type: str) -> None:
        """Reject connector types with no real driver in this deployment (422)
        even when the probe is skipped — a driverless connection must never be
        persisted only to ingest nothing later."""
        if isinstance(self.c.probers.get(connector_type), UnsupportedConnectorProber):
            raise UnsupportedConnectorError(connector_type)

    async def _probe(
        self, connector_type: str, config_model, secrets: dict[str, str]
    ) -> ProbeResult:
        """Run the probe under the ING-FR-004 15s connect timeout."""
        prober = self.c.probers.get(connector_type)
        try:
            return await asyncio.wait_for(
                prober.probe(config_model, secrets),
                timeout=self.c.settings.connection_test_timeout_s,
            )
        except TimeoutError:
            return ProbeResult(
                "failed",
                int(self.c.settings.connection_test_timeout_s * 1000),
                error_category=ErrorCategory.TIMEOUT,
                error_detail="connection test timed out",
            )

    async def _probe_or_raise(
        self, connector_type: str, config_model, secrets: dict[str, str]
    ) -> ProbeResult:
        result = await self._probe(connector_type, config_model, secrets)
        if not result.ok:
            raise ConnectionTestFailedError(
                "connection test failed",
                error_category=result.error_category or "INTERNAL",
                error_detail=result.error_detail,
            )
        return result

    async def _get(self, session, principal: Principal, connection_id: str) -> Connection:
        conn = (
            await session.execute(
                sa.select(Connection).where(
                    Connection.id == connection_id,
                    Connection.tenant_id == principal.tenant_id,
                    Connection.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if conn is None:
            await raise_not_found_with_audit(
                session, principal, Connection, connection_id, "connection"
            )
        return conn

    async def _check_name_free(
        self, session, tenant_id: str, workspace_id: str, name: str, exclude_id: str | None = None
    ) -> None:
        stmt = sa.select(Connection.id).where(
            Connection.tenant_id == tenant_id,
            Connection.workspace_id == workspace_id,
            sa.func.lower(Connection.name) == name.lower(),
            Connection.deleted_at.is_(None),
        )
        if exclude_id:
            stmt = stmt.where(Connection.id != exclude_id)
        if (await session.execute(stmt)).first() is not None:
            raise ConflictError(f"connection name {name!r} already exists in workspace")

    async def create(self, principal: Principal, body: ConnectionCreate) -> dict[str, Any]:
        self._ensure_supported(body.connector_type)
        config_model = connectors.validate_config(body.connector_type, body.config)
        secrets = connectors.validate_secrets(body.connector_type, body.secrets)

        test_status: str | None = None
        tested_at: datetime | None = None
        if not body.skip_test:  # AC-2: failed probe -> 424, nothing persisted
            await self._probe_or_raise(body.connector_type, config_model, secrets)
            test_status, tested_at = "ok", datetime.now(UTC)

        connection_id = uuid7()
        vault_ref = connection_secret_path(principal.tenant_id, connection_id) if secrets else None

        async with self.c.db.tenant_session(principal.tenant_id) as session:
            await self._check_name_free(session, principal.tenant_id, body.workspace_id, body.name)
            conn = Connection(
                id=connection_id,
                tenant_id=principal.tenant_id,
                workspace_id=body.workspace_id,
                name=body.name,
                connector_type=body.connector_type,
                config=connectors.dump_config(config_model),
                vault_ref=vault_ref,
                secret_field_names=sorted(secrets),
                traffic_direction=body.traffic_direction,
                tags=body.tags,
                last_test_status=test_status,
                last_tested_at=tested_at,
                created_by=principal.sub,
            )
            session.add(conn)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="connection.created",
                resource_urn=connection_urn(conn),
                payload={"connection_id": conn.id, "connector_type": conn.connector_type},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            try:
                await session.commit()
            except IntegrityError as exc:  # lost the unique-name race (F5: no secret yet)
                raise ConflictError(
                    f"connection name {body.name!r} already exists in workspace"
                ) from exc

        # F5: persist the secret only after the row is durably committed, so a
        # 409/DB failure never leaves an orphaned secret in the store.
        if secrets:
            await self.c.secrets.put(vault_ref, secrets)
        return serialize_connection(conn)

    async def get(self, principal: Principal, connection_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = await self._get(session, principal, connection_id)
            return serialize_connection(conn)

    async def list(
        self,
        principal: Principal,
        *,
        connector_type: str | None,
        traffic_direction: str | None,
        q: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from app.api.pagination import paginate

        stmt = sa.select(Connection).where(
            Connection.tenant_id == principal.tenant_id, Connection.deleted_at.is_(None)
        )
        if connector_type:
            stmt = stmt.where(Connection.connector_type == connector_type)
        if traffic_direction:
            stmt = stmt.where(Connection.traffic_direction == traffic_direction)
        if q:
            stmt = stmt.where(sa.func.lower(Connection.name).contains(q.lower()))
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            items, page = await paginate(session, stmt, Connection.id, limit=limit, cursor=cursor)
            return [serialize_connection(c) for c in items], page

    async def update(
        self, principal: Principal, connection_id: str, body: ConnectionUpdate
    ) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = await self._get(session, principal, connection_id)

            config_changed = body.config is not None
            secrets_changed = body.secrets is not None
            new_config_model = connectors.validate_config(
                conn.connector_type, body.config if config_changed else conn.config
            )
            new_secret_fields = sorted(conn.secret_field_names)
            merged_secrets: dict[str, str] = {}
            if conn.vault_ref:
                merged_secrets = await self.c.secrets.get(conn.vault_ref) or {}
            if secrets_changed:
                incoming = connectors.validate_secrets(conn.connector_type, body.secrets or {})
                merged_secrets = {**merged_secrets, **incoming}
                new_secret_fields = sorted(merged_secrets)

            if (config_changed or secrets_changed) and not body.skip_test:
                await self._probe_or_raise(conn.connector_type, new_config_model, merged_secrets)
                conn.last_test_status = "ok"
                conn.last_tested_at = datetime.now(UTC)

            if body.name is not None and body.name.lower() != conn.name.lower():
                await self._check_name_free(
                    session, principal.tenant_id, conn.workspace_id, body.name, exclude_id=conn.id
                )
            if body.name is not None:
                conn.name = body.name
            if config_changed:
                conn.config = connectors.dump_config(new_config_model)
            if secrets_changed:
                vault_ref = conn.vault_ref or connection_secret_path(principal.tenant_id, conn.id)
                await self.c.secrets.put(vault_ref, merged_secrets)  # US-6 rotation
                conn.vault_ref = vault_ref
                conn.secret_field_names = new_secret_fields
            if body.traffic_direction is not None:
                conn.traffic_direction = body.traffic_direction
            if body.tags is not None:
                conn.tags = body.tags

            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="connection.updated",
                resource_urn=connection_urn(conn),
                payload={"connection_id": conn.id},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            await session.commit()
            return serialize_connection(conn)

    async def delete(self, principal: Principal, connection_id: str) -> None:
        """ING-FR-006 + BR-12: refuse while in use; cascade soft-delete schedules."""
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = await self._get(session, principal, connection_id)

            active_ingestions = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Ingestion)
                    .where(
                        Ingestion.tenant_id == principal.tenant_id,
                        Ingestion.connection_id == conn.id,
                        Ingestion.status.notin_(TERMINAL_STATUSES),
                    )
                )
            ).scalar_one()
            enabled_schedules = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Schedule)
                    .where(
                        Schedule.tenant_id == principal.tenant_id,
                        Schedule.connection_id == conn.id,
                        Schedule.enabled.is_(True),
                        Schedule.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
            if active_ingestions or enabled_schedules:
                raise ConflictError(
                    "connection is in use",
                    details={
                        "active_ingestions": active_ingestions,
                        "enabled_schedules": enabled_schedules,
                    },
                )

            now = datetime.now(UTC)
            conn.deleted_at = now
            await session.execute(
                sa.update(Schedule)
                .where(
                    Schedule.tenant_id == principal.tenant_id,
                    Schedule.connection_id == conn.id,
                    Schedule.deleted_at.is_(None),
                )
                .values(deleted_at=now, enabled=False)
            )
            if conn.vault_ref:  # destroy after 7-day grace (ING-FR-006)
                destroy_at = now + timedelta(days=self.c.settings.vault_destroy_grace_days)
                await self.c.secrets.schedule_destroy(conn.vault_ref, destroy_at)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="connection.deleted",
                resource_urn=connection_urn(conn),
                payload={"connection_id": conn.id},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            await session.commit()

    async def test_saved(self, principal: Principal, connection_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = await self._get(session, principal, connection_id)
            config_model = connectors.validate_config(conn.connector_type, conn.config)
            secrets = (await self.c.secrets.get(conn.vault_ref) or {}) if conn.vault_ref else {}
            result = await self._probe(conn.connector_type, config_model, secrets)
            conn.last_test_status = result.status
            conn.last_tested_at = datetime.now(UTC)
            if not result.ok:
                emit_event(
                    session,
                    tenant_id=principal.tenant_id,
                    event_type="connection.test_failed",
                    resource_urn=connection_urn(conn),
                    payload={
                        "connection_id": conn.id,
                        "error_category": result.error_category,
                    },
                    actor=principal.actor(),
                    via_agent=principal.via_agent(),
                )
            await session.commit()
            return {
                "status": result.status,
                "latency_ms": result.latency_ms,
                "error_category": result.error_category,
                "error_detail": result.error_detail,
            }

    async def test_adhoc(self, principal: Principal, body: ConnectionTestAdhoc) -> dict[str, Any]:
        self._ensure_supported(body.connector_type)
        config_model = connectors.validate_config(body.connector_type, body.config)
        secrets = connectors.validate_secrets(body.connector_type, body.secrets)
        result = await self._probe(body.connector_type, config_model, secrets)
        return {
            "status": result.status,
            "latency_ms": result.latency_ms,
            "error_category": result.error_category,
            "error_detail": result.error_detail,
        }

    async def preview(
        self, principal: Principal, connection_id: str, body: PreviewRequest
    ) -> dict[str, Any]:
        """ING-FR-005: <=100 rows, never persisted."""
        if not (body.table or body.path or body.query):
            raise ValidationFailedError(
                "preview target required",
                details=[{"field": "table", "message": "one of table/path/query is required"}],
            )
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = await self._get(session, principal, connection_id)
            config_model = connectors.validate_config(conn.connector_type, conn.config)
            secrets = (await self.c.secrets.get(conn.vault_ref) or {}) if conn.vault_ref else {}
        try:  # ING-FR-005: 30s preview timeout -> 408 TIMEOUT
            result = await asyncio.wait_for(
                self.c.previewer.preview(
                    config_model, secrets, body.model_dump(exclude_none=True), body.limit
                ),
                timeout=self.c.settings.preview_timeout_s,
            )
        except TimeoutError as exc:
            raise RequestTimeoutError(
                "source preview timed out", details={"timeout_s": self.c.settings.preview_timeout_s}
            ) from exc
        return {"columns": result.columns, "rows": result.rows[: body.limit]}

    async def get_or_404_model(
        self, session, principal: Principal, connection_id: str
    ) -> Connection:
        return await self._get(session, principal, connection_id)


async def load_connection_secrets(container: Container, conn: Connection) -> dict[str, str]:
    if not conn.vault_ref:
        return {}
    return await container.secrets.get(conn.vault_ref) or {}


def not_found_if_deleted(conn: Connection | None) -> Connection:
    if conn is None or conn.deleted_at is not None:
        raise NotFoundError()
    return conn

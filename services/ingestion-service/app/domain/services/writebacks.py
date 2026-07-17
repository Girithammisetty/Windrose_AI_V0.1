"""Decision write-back (INS-FR-061 SoR write adapters).

A `writeback` is a governed, idempotent, proposal-mode delivery of a platform
decision (e.g. a case disposition) to a tenant's system of record via an
`outgoing` connection. Two real executors, no stubs:

- ``db_upsert``  (connector_type=postgres) — a real ``INSERT ... ON CONFLICT
  (key) DO UPDATE`` into the target table, values bound (never interpolated).
- ``http_post``  (connector_type=http_api) — a real ``httpx`` POST/PUT with an
  ``Idempotency-Key`` header.

Governance: all external writes are proposal-mode. A write-back enters
``pending_approval``; a DISTINCT approver (≠ requester) must approve before any
external write (four-eyes), unless the job is ``approval_mode=auto``. Delivery
status is durable and retryable; every delivery emits an audit event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import sqlalchemy as sa

from app.api.auth import Principal
from app.api.schemas import WritebackCreate
from app.container import Container
from app.domain.drivers.http import _guard_host, _guard_url
from app.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from app.domain.secrets import connection_secret_path
from app.domain.services.common import iso
from app.events.outbox import emit_event
from app.store.models import Connection, Writeback

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# connector_type -> executor kind
_EXECUTORS = {"postgres": "db_upsert", "http_api": "http_post"}


def writeback_urn(wb: Writeback) -> str:
    return f"wr:{wb.tenant_id}:ingestion:writeback/{wb.id}"


def serialize_writeback(wb: Writeback) -> dict[str, Any]:
    return {
        "id": wb.id,
        "connection_id": wb.connection_id,
        "workspace_id": wb.workspace_id,
        "decision_kind": wb.decision_kind,
        "decision_ref": wb.decision_ref,
        "idempotency_key": wb.idempotency_key,
        "target": wb.target,
        "payload": wb.payload,
        "status": wb.status,
        "approval_mode": wb.approval_mode,
        "requested_by": wb.requested_by,
        "approved_by": wb.approved_by,
        "attempts": wb.attempts,
        "last_error": wb.last_error,
        "target_ref": wb.target_ref,
        "delivered_at": iso(wb.delivered_at),
        "created_at": iso(wb.created_at),
        "updated_at": iso(wb.updated_at),
    }


def _quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValidationFailedError(f"invalid identifier {name!r}")
    return '"' + name + '"'


@dataclass
class _ConnSnapshot:
    """A detached copy of the delivery-relevant connection fields, captured
    before the platform DB session is released so external I/O never runs while
    a pooled tenant session is held open (pool-exhaustion guard)."""

    id: str
    tenant_id: str
    connector_type: str
    config: dict[str, Any]
    vault_ref: str | None


@dataclass
class _WbSnapshot:
    target: dict[str, Any]
    payload: dict[str, Any]
    idempotency_key: str


class WritebackService:
    def __init__(self, container: Container):
        self.c = container

    async def _get_connection(self, session, connection_id: str) -> Connection:
        conn = (
            await session.execute(sa.select(Connection).where(Connection.id == connection_id))
        ).scalar_one_or_none()
        if conn is None or conn.deleted_at is not None:
            raise NotFoundError("connection not found")
        return conn

    async def _get(self, session, writeback_id: str) -> Writeback:
        wb = (
            await session.execute(sa.select(Writeback).where(Writeback.id == writeback_id))
        ).scalar_one_or_none()
        if wb is None:
            raise NotFoundError("writeback not found")
        return wb

    # ---- enqueue -------------------------------------------------------------
    async def enqueue(self, principal: Principal, body: WritebackCreate) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = await self._get_connection(session, body.connection_id)
            if conn.traffic_direction not in ("outgoing", "both"):
                raise ValidationFailedError(
                    "connection is not an outgoing write-back target "
                    f"(traffic_direction={conn.traffic_direction})"
                )
            if conn.connector_type not in _EXECUTORS:
                raise ValidationFailedError(
                    f"connector_type {conn.connector_type!r} has no write-back executor "
                    f"(supported: {', '.join(sorted(_EXECUTORS))})"
                )
            # Idempotent by (tenant, connection, idempotency_key): re-enqueue of
            # the same decision returns the existing job rather than duplicating.
            existing = (
                await session.execute(
                    sa.select(Writeback).where(
                        Writeback.connection_id == body.connection_id,
                        Writeback.idempotency_key == body.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return serialize_writeback(existing)

            # Governance: every external write is proposal-mode and four-eyes.
            # The requester may NOT self-select `auto` delivery — that would let
            # a single actor with `writeback.create` push a decision into the
            # tenant's SoR with no second-party approval, defeating the control
            # this subsystem exists to enforce. Auto-delivery, if ever offered,
            # is an admin-set connection policy, never a per-request field.
            wb = Writeback(
                tenant_id=principal.tenant_id,
                workspace_id=body.workspace_id,
                connection_id=body.connection_id,
                decision_kind=body.decision_kind,
                decision_ref=body.decision_ref,
                idempotency_key=body.idempotency_key,
                target=body.target,
                payload=body.payload,
                status="pending_approval",
                approval_mode="four_eyes",
                requested_by=principal.sub,
            )
            session.add(wb)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="ingestion.writeback.requested",
                resource_urn=writeback_urn(wb),
                payload={"connection_id": wb.connection_id, "decision_ref": wb.decision_ref,
                         "approval_mode": wb.approval_mode},
                actor={"type": "user", "id": principal.sub},
            )
            await session.commit()
            return serialize_writeback(wb)

    # ---- list / get ----------------------------------------------------------
    async def list(
        self, principal: Principal, status: str | None, workspace_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            stmt = sa.select(Writeback).order_by(Writeback.created_at.desc()).limit(limit)
            if status:
                stmt = stmt.where(Writeback.status == status)
            if workspace_id:
                stmt = stmt.where(Writeback.workspace_id == workspace_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [serialize_writeback(w) for w in rows]

    async def get(self, principal: Principal, writeback_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            return serialize_writeback(await self._get(session, writeback_id))

    # ---- approve / reject / retry -------------------------------------------
    async def approve(self, principal: Principal, writeback_id: str) -> dict[str, Any]:
        # Phase 1 (short txn): four-eyes check + claim the job (status=delivering),
        # snapshot everything delivery needs, then RELEASE the pooled tenant
        # session BEFORE any external I/O. Holding it across a slow/unreachable
        # SoR would park the connection for up to the delivery timeout and, under
        # concurrent approvals, exhaust the pool (pool-exhaustion guard).
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            wb = await self._get(session, writeback_id)
            if wb.status != "pending_approval":
                raise ConflictError(f"writeback is {wb.status}, not pending_approval")
            # Four-eyes: the approver must be a DISTINCT subject from the requester.
            if wb.requested_by and principal.sub == wb.requested_by:
                raise ValidationFailedError(
                    "four-eyes: the approver must differ from the requester"
                )
            wb.approved_by = principal.sub
            conn_snap, wb_snap, kind = await self._claim_for_delivery(session, wb)
            await session.commit()
        return await self._deliver_and_record(principal, writeback_id, conn_snap, wb_snap, kind)

    async def reject(self, principal: Principal, writeback_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            wb = await self._get(session, writeback_id)
            if wb.status not in ("pending_approval", "failed"):
                raise ConflictError(f"writeback is {wb.status}; cannot reject")
            wb.status = "rejected"
            wb.updated_at = datetime.now(UTC)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="ingestion.writeback.rejected",
                resource_urn=writeback_urn(wb),
                payload={"decision_ref": wb.decision_ref},
                actor={"type": "user", "id": principal.sub},
            )
            await session.commit()
            return serialize_writeback(wb)

    async def retry(self, principal: Principal, writeback_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            wb = await self._get(session, writeback_id)
            # "delivering" is retryable too: a crash between claim and record can
            # strand a row there, and both executors are idempotent (HTTP
            # Idempotency-Key / ON CONFLICT upsert) so a re-drive is safe.
            if wb.status not in ("failed", "delivering"):
                raise ConflictError(f"writeback is {wb.status}, not failed")
            if not wb.approved_by:
                raise ValidationFailedError("writeback was never approved")
            conn_snap, wb_snap, kind = await self._claim_for_delivery(session, wb)
            await session.commit()
        return await self._deliver_and_record(principal, writeback_id, conn_snap, wb_snap, kind)

    # ---- delivery (real executors) ------------------------------------------
    async def _claim_for_delivery(
        self, session, wb: Writeback
    ) -> tuple[_ConnSnapshot, _WbSnapshot, str]:
        """Mark the job delivering and snapshot the connection + payload while the
        session is still open, so external I/O can run with the session released."""
        conn = await self._get_connection(session, wb.connection_id)
        kind = _EXECUTORS.get(conn.connector_type)
        if kind is None:
            raise ValidationFailedError(
                f"connector_type {conn.connector_type!r} has no write-back executor"
            )
        wb.status = "delivering"
        wb.attempts += 1
        wb.updated_at = datetime.now(UTC)
        conn_snap = _ConnSnapshot(
            id=conn.id,
            tenant_id=conn.tenant_id,
            connector_type=conn.connector_type,
            config=dict(conn.config or {}),
            vault_ref=conn.vault_ref,
        )
        wb_snap = _WbSnapshot(
            target=dict(wb.target or {}),
            payload=dict(wb.payload or {}),
            idempotency_key=wb.idempotency_key,
        )
        return conn_snap, wb_snap, kind

    async def _deliver_and_record(
        self,
        principal: Principal,
        writeback_id: str,
        conn: _ConnSnapshot,
        wb_snap: _WbSnapshot,
        kind: str,
    ) -> dict[str, Any]:
        """Phase 2: run the external delivery with NO platform session held, then
        Phase 3: record the durable outcome + audit event in a fresh short txn."""
        target_ref: str | None = None
        error: str | None = None
        try:
            if kind == "db_upsert":
                target_ref = await self._deliver_db_upsert(conn, wb_snap)
            else:
                target_ref = await self._deliver_http_post(conn, wb_snap)
        except Exception as exc:  # noqa: BLE001 — failure is a first-class state
            # Store the exception type + our own message only; NEVER echo an
            # upstream response body (that would turn SSRF into an exfil oracle
            # via last_error, which is readable through GET /writebacks/{id}).
            error = f"{type(exc).__name__}: {exc}"[:2000]

        async with self.c.db.tenant_session(principal.tenant_id) as session:
            wb = await self._get(session, writeback_id)
            if error is None:
                wb.status = "delivered"
                wb.target_ref = target_ref
                wb.last_error = None
                wb.delivered_at = datetime.now(UTC)
                wb.updated_at = datetime.now(UTC)
                emit_event(
                    session,
                    tenant_id=principal.tenant_id,
                    event_type="ingestion.writeback.delivered",
                    resource_urn=writeback_urn(wb),
                    payload={"connection_id": conn.id, "decision_ref": wb.decision_ref,
                             "target_ref": target_ref, "approved_by": wb.approved_by},
                    actor={"type": "user", "id": principal.sub},
                )
            else:
                wb.status = "failed"
                wb.last_error = error
                wb.updated_at = datetime.now(UTC)
                emit_event(
                    session,
                    tenant_id=principal.tenant_id,
                    event_type="ingestion.writeback.failed",
                    resource_urn=writeback_urn(wb),
                    payload={"connection_id": conn.id, "decision_ref": wb.decision_ref,
                             "error": wb.last_error},
                    actor={"type": "user", "id": principal.sub},
                )
            await session.commit()
            return serialize_writeback(wb)

    async def _connection_secrets(self, conn: _ConnSnapshot) -> dict[str, str]:
        if not conn.vault_ref:
            return {}
        path = conn.vault_ref or connection_secret_path(conn.tenant_id, conn.id)
        return (await self.c.secrets.get(path)) or {}

    async def _deliver_db_upsert(self, conn: _ConnSnapshot, wb: _WbSnapshot) -> str:
        """Real INSERT ... ON CONFLICT (key) DO UPDATE into the target table.
        Column names are validated + quoted; VALUES are bound parameters."""
        schema = wb.target.get("schema") or "public"
        table = wb.target.get("table")
        key = wb.target.get("key_column")
        if not table or not key:
            raise ValidationFailedError("db_upsert target requires {table, key_column}")
        row = dict(wb.payload or {})
        if key not in row:
            raise ValidationFailedError(f"payload is missing the key column {key!r}")
        cols = list(row.keys())
        q_schema, q_table = _quote_ident(schema), _quote_ident(table)
        q_cols = [_quote_ident(c) for c in cols]
        placeholders = [f"${i + 1}" for i in range(len(cols))]
        updates = ", ".join(
            f"{qc} = EXCLUDED.{qc}" for qc, c in zip(q_cols, cols, strict=True) if c != key
        )
        conflict = f"ON CONFLICT ({_quote_ident(key)}) DO UPDATE SET {updates}" if updates \
            else f"ON CONFLICT ({_quote_ident(key)}) DO NOTHING"
        sql = (
            f"INSERT INTO {q_schema}.{q_table} ({', '.join(q_cols)}) "
            f"VALUES ({', '.join(placeholders)}) {conflict}"
        )
        secrets = await self._connection_secrets(conn)
        config = conn.config or {}
        # SSRF guard: reject a target host that resolves to a link-local
        # (cloud-metadata) or private, non-loopback address before connecting.
        _guard_host(config.get("host"))
        pg = await asyncpg.connect(
            host=config.get("host"),
            port=config.get("port", 5432),
            user=config.get("username"),
            password=secrets.get("password"),
            database=config.get("database"),
            ssl=False if config.get("ssl_mode", "prefer") in ("disable", None)
            else config.get("ssl_mode"),
            timeout=15,
        )
        try:
            await pg.execute(sql, *[row[c] for c in cols])
        finally:
            await pg.close()
        return f"{schema}.{table}[{key}={row[key]}]"

    async def _deliver_http_post(self, conn: _ConnSnapshot, wb: _WbSnapshot) -> str:
        """Real HTTP POST/PUT to the target URL with an Idempotency-Key."""
        config = conn.config or {}
        base = (config.get("url") or config.get("base_url") or "").rstrip("/")
        if not base:
            raise ValidationFailedError("http_post connection config missing url")
        path = (wb.target.get("path") or "").lstrip("/")
        url = f"{base}/{path}" if path else base
        method = (wb.target.get("method") or config.get("method") or "POST").upper()
        headers = {"Content-Type": "application/json", "Idempotency-Key": wb.idempotency_key}
        secrets = await self._connection_secrets(conn)
        if secrets.get("token"):
            headers["Authorization"] = f"Bearer {secrets['token']}"
        # SSRF guard: reject non-http(s) schemes and hosts resolving to
        # link-local / private (non-loopback) addresses. follow_redirects=False
        # so a 3xx can't bounce the request to an internal target post-guard.
        _guard_url(url)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            resp = await client.request(method, url, json=wb.payload, headers=headers)
        if resp.status_code >= 300:
            # Do NOT include resp.text — last_error is readable via the API and
            # echoing an upstream body would make this an SSRF exfil oracle.
            raise ValidationFailedError(f"target returned {resp.status_code}")
        return f"{method} {url} -> {resp.status_code}"

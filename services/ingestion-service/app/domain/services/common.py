"""Shared service helpers: cross-tenant 404 + audit (MASTER-FR-003)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal
from app.domain.errors import NotFoundError
from app.events.outbox import emit_event


async def _owner_tenant(session: AsyncSession, model: type[Any], resource_id: str) -> str | None:
    """Return the tenant that owns `resource_id`, bypassing RLS on Postgres.

    On Postgres the SECURITY DEFINER `ing_owner_tenant` function (migration 0002)
    sees rows in any tenant, so a cross-tenant row is reliably detected under the
    non-superuser app role. On SQLite (no RLS) a plain lookup suffices.
    """
    dialect = session.get_bind().dialect.name
    try:
        if dialect == "postgresql":
            row = (
                await session.execute(
                    sa.text("SELECT ing_owner_tenant(:t, CAST(:id AS uuid))"),
                    {"t": model.__tablename__, "id": resource_id},
                )
            ).first()
        else:
            row = (
                await session.execute(sa.select(model.tenant_id).where(model.id == resource_id))
            ).first()
    except Exception:  # malformed uuid etc. — treat as plain 404
        return None
    if row is None or row[0] is None:
        return None
    return str(row[0])


async def raise_not_found_with_audit(
    session: AsyncSession,
    principal: Principal,
    model: type[Any],
    resource_id: str,
    resource_type: str,
) -> None:
    """Cross-tenant access returns 404 (never 403) and emits
    `security.cross_tenant_denied` when the id belongs to another tenant
    (MASTER-FR-003). Detection works under production RLS via `ing_owner_tenant`.
    """
    owner = await _owner_tenant(session, model, resource_id)
    if owner is not None and owner != principal.tenant_id:
        emit_event(
            session,
            tenant_id=principal.tenant_id,
            event_type="security.cross_tenant_denied",
            resource_urn=f"wr:{principal.tenant_id}:ingestion:{resource_type}/{resource_id}",
            payload={"resource_type": resource_type, "resource_id": resource_id},
            actor=principal.actor(),
            via_agent=principal.via_agent(),
        )
        await session.commit()
    raise NotFoundError()


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None

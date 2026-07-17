"""Transition recorder (ING-FR-022, §4.3).

Validates the guarded transition, mutates the row, appends the
ingestion_transitions record, and emits the mapped event — all in the caller's
session so the outbox write shares the state-change transaction
(MASTER-FR-034).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.state_machine import TransitionContext, validate_transition
from app.events.outbox import emit_event
from app.ids import uuid7
from app.store.models import Ingestion, IngestionTransition

# §6 catalog events; other transitions emit the generic ingestion.transitioned.
EVENT_BY_STATUS: dict[str, str] = {
    "running": "ingestion.started",
    "completed": "ingestion.completed",
    "failed": "ingestion.failed",
    "cancelled": "ingestion.cancelled",
    "expired": "upload.expired",
}


def ingestion_urn(ingestion: Ingestion) -> str:
    return f"wr:{ingestion.tenant_id}:ingestion:ingestion/{ingestion.id}"


def record_transition(
    session: AsyncSession,
    ingestion: Ingestion,
    to_status: str,
    ctx: TransitionContext,
    *,
    actor: dict[str, Any] | None = None,
    via_agent: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    event_payload: dict[str, Any] | None = None,
) -> None:
    from_status = ingestion.status
    validate_transition(from_status, to_status, ctx)
    ingestion.status = to_status
    session.add(
        IngestionTransition(
            id=uuid7(),
            tenant_id=ingestion.tenant_id,
            ingestion_id=ingestion.id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            detail=detail,
        )
    )
    event_type = EVENT_BY_STATUS.get(to_status, "ingestion.transitioned")
    payload = event_payload or {
        "ingestion_id": ingestion.id,
        "from_status": from_status,
        "to_status": to_status,
    }
    emit_event(
        session,
        tenant_id=ingestion.tenant_id,
        event_type=event_type,
        resource_urn=ingestion_urn(ingestion),
        payload=payload,
        actor=actor,
        via_agent=via_agent,
    )

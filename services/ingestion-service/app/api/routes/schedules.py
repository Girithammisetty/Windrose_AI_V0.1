"""Schedule endpoints (ING-FR-060/063)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from app.api.deps import ContainerDep, PrincipalDep, tenant_urn
from app.api.schemas import ScheduleCreate, ScheduleUpdate
from app.domain.policy import authorize
from app.domain.services.schedules import ScheduleService

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.post("", status_code=201)
async def create_schedule(
    body: ScheduleCreate, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.create",
        tenant_urn(principal.tenant_id, "schedule", "*"),
    )
    return {"data": await ScheduleService(container).create(principal, body)}


@router.get("")
async def list_schedules(
    principal: PrincipalDep,
    container: ContainerDep,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.read",
        tenant_urn(principal.tenant_id, "schedule", "*"),
    )
    items, page = await ScheduleService(container).list(principal, limit=limit, cursor=cursor)
    return {"data": items, "page": page}


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.read",
        tenant_urn(principal.tenant_id, "schedule", schedule_id),
    )
    return {"data": await ScheduleService(container).get(principal, schedule_id)}


@router.patch("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.update",
        tenant_urn(principal.tenant_id, "schedule", schedule_id),
    )
    return {"data": await ScheduleService(container).update(principal, schedule_id, body)}


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str, principal: PrincipalDep, container: ContainerDep
) -> Response:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.delete",
        tenant_urn(principal.tenant_id, "schedule", schedule_id),
    )
    await ScheduleService(container).delete(principal, schedule_id)
    return Response(status_code=204)


@router.post("/{schedule_id}/run_now")
async def run_now(
    schedule_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.execute",
        tenant_urn(principal.tenant_id, "schedule", schedule_id),
    )
    return {"data": await ScheduleService(container).run_now(principal, schedule_id)}


@router.post("/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.update",
        tenant_urn(principal.tenant_id, "schedule", schedule_id),
    )
    return {"data": await ScheduleService(container).pause(principal, schedule_id)}


@router.post("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.schedule.update",
        tenant_urn(principal.tenant_id, "schedule", schedule_id),
    )
    return {"data": await ScheduleService(container).resume(principal, schedule_id)}

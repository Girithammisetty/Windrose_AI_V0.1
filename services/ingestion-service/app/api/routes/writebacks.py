"""Decision write-back routes (INS-FR-061).

Governed, proposal-mode delivery of platform decisions to a tenant's system of
record via `outgoing` connections. Enqueue is idempotent; approval is
four-eyes; delivery is real (db_upsert / http_post).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep, PrincipalDep, tenant_urn
from app.api.schemas import WritebackCreate
from app.domain.policy import authorize
from app.domain.services.idempotency import request_hash, run_idempotent
from app.domain.services.writebacks import WritebackService

router = APIRouter(tags=["writebacks"])


@router.post("/writebacks", status_code=201)
async def create_writeback(
    body: WritebackCreate,
    principal: PrincipalDep,
    container: ContainerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    await authorize(
        container.policy, principal, "ingestion.writeback.create",
        tenant_urn(principal.tenant_id, "writeback", "*"),
    )
    svc = WritebackService(container)

    async def handler() -> tuple[int, dict[str, Any]]:
        return 201, {"data": await svc.enqueue(principal, body)}

    status, payload, replayed = await run_idempotent(
        container.db, principal.tenant_id, idempotency_key,
        request_hash(body.model_dump()), handler,
    )
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(status_code=status, content=payload, headers=headers)


@router.get("/writebacks")
async def list_writebacks(
    principal: PrincipalDep,
    container: ContainerDep,
    status: Annotated[str | None, Query()] = None,
    workspace_id: Annotated[str | None, Query(alias="filter[workspace_id]")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    await authorize(
        container.policy, principal, "ingestion.writeback.read",
        tenant_urn(principal.tenant_id, "writeback", "*"),
    )
    svc = WritebackService(container)
    return {"data": await svc.list(principal, status, workspace_id, limit)}


@router.get("/writebacks/{writeback_id}")
async def get_writeback(
    writeback_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy, principal, "ingestion.writeback.read",
        tenant_urn(principal.tenant_id, "writeback", writeback_id),
    )
    svc = WritebackService(container)
    return {"data": await svc.get(principal, writeback_id)}


@router.post("/writebacks/{writeback_id}/approve")
async def approve_writeback(
    writeback_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy, principal, "ingestion.writeback.approve",
        tenant_urn(principal.tenant_id, "writeback", writeback_id),
    )
    svc = WritebackService(container)
    return {"data": await svc.approve(principal, writeback_id)}


@router.post("/writebacks/{writeback_id}/reject")
async def reject_writeback(
    writeback_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy, principal, "ingestion.writeback.approve",
        tenant_urn(principal.tenant_id, "writeback", writeback_id),
    )
    svc = WritebackService(container)
    return {"data": await svc.reject(principal, writeback_id)}


@router.post("/writebacks/{writeback_id}/retry")
async def retry_writeback(
    writeback_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy, principal, "ingestion.writeback.execute",
        tenant_urn(principal.tenant_id, "writeback", writeback_id),
    )
    svc = WritebackService(container)
    return {"data": await svc.retry(principal, writeback_id)}

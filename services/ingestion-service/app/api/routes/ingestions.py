"""Ingestion job endpoints (BRD 03 §5)."""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep, PrincipalDep, tenant_urn
from app.api.schemas import IngestionCreate
from app.domain.policy import authorize
from app.domain.services.idempotency import request_hash, run_idempotent
from app.domain.services.ingestions import IngestionService

router = APIRouter(prefix="/ingestions", tags=["ingestions"])


@router.post("")
async def create_ingestion(
    body: IngestionCreate,
    principal: PrincipalDep,
    container: ContainerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.create",
        tenant_urn(principal.tenant_id, "ingestion", "*"),
    )
    svc = IngestionService(container)

    async def handler() -> tuple[int, dict[str, Any]]:
        status, data = await svc.create(principal, body)
        return status, {"data": data}

    status, payload, replayed = await run_idempotent(
        container.db,
        principal.tenant_id,
        idempotency_key,
        request_hash(body.model_dump()),
        handler,
    )
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(status_code=status, content=payload, headers=headers)


@router.get("")
async def list_ingestions(
    principal: PrincipalDep,
    container: ContainerDep,
    limit: int | None = None,
    cursor: str | None = None,
    status: Annotated[str | None, Query(alias="filter[status]")] = None,
    dataset_urn: Annotated[str | None, Query(alias="filter[dataset_urn]")] = None,
    ingestion_mode: Annotated[str | None, Query(alias="filter[ingestion_mode]")] = None,
    schedule_id: Annotated[str | None, Query(alias="filter[schedule_id]")] = None,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.read",
        tenant_urn(principal.tenant_id, "ingestion", "*"),
    )
    items, page = await IngestionService(container).list(
        principal,
        status=status,
        dataset_urn=dataset_urn,
        mode=ingestion_mode,
        schedule_id=schedule_id,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "page": page}


@router.get("/{ingestion_id}")
async def get_ingestion(
    ingestion_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.read",
        tenant_urn(principal.tenant_id, "ingestion", ingestion_id),
    )
    return {"data": await IngestionService(container).get(principal, ingestion_id)}


@router.get("/{ingestion_id}/progress")
async def get_ingestion_progress(
    ingestion_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    """Progress snapshot + recent progress events (ING-FR-026).

    Live streaming is relayed by realtime-hub from `ingestion.events.v1`;
    this endpoint serves the non-streaming fallback.
    """
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.read",
        tenant_urn(principal.tenant_id, "ingestion", ingestion_id),
    )
    snapshot = await IngestionService(container).get(principal, ingestion_id)
    from app.store.models import OutboxEvent

    async with container.db.tenant_session(principal.tenant_id) as session:
        events = (
            (
                await session.execute(
                    sa.select(OutboxEvent)
                    .where(
                        OutboxEvent.tenant_id == principal.tenant_id,
                        OutboxEvent.event_type == "ingestion.progress",
                        OutboxEvent.resource_urn
                        == f"wr:{principal.tenant_id}:ingestion:ingestion/{ingestion_id}",
                    )
                    .order_by(OutboxEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    return {
        "data": {
            "snapshot": snapshot,
            "events": [e.payload for e in events],
        }
    }


@router.post("/{ingestion_id}/cancel")
async def cancel_ingestion(
    ingestion_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.execute",
        tenant_urn(principal.tenant_id, "ingestion", ingestion_id),
    )
    return {"data": await IngestionService(container).cancel(principal, ingestion_id)}


@router.post("/{ingestion_id}/retry")
async def retry_ingestion(
    ingestion_id: str, principal: PrincipalDep, container: ContainerDep
) -> JSONResponse:
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.execute",
        tenant_urn(principal.tenant_id, "ingestion", ingestion_id),
    )
    status, data = await IngestionService(container).retry(principal, ingestion_id)
    return JSONResponse(status_code=status, content={"data": data})


@router.post("/{ingestion_id}/reingest")
async def reingest_ingestion(
    ingestion_id: str, principal: PrincipalDep, container: ContainerDep
) -> JSONResponse:
    await authorize(
        container.policy,
        principal,
        "ingestion.ingestion.create",
        tenant_urn(principal.tenant_id, "ingestion", ingestion_id),
    )
    status, data = await IngestionService(container).reingest(principal, ingestion_id)
    return JSONResponse(status_code=status, content={"data": data})

"""Connection endpoints (BRD 03 §5)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep, PrincipalDep, tenant_urn
from app.api.schemas import (
    ConnectionCreate,
    ConnectionTestAdhoc,
    ConnectionUpdate,
    PreviewRequest,
)
from app.domain.connectors import (
    CONNECTOR_TYPES,
    connector_catalog,
    connector_catalog_entry,
)
from app.domain.errors import NotFoundError
from app.domain.policy import authorize
from app.domain.services.connections import ConnectionService
from app.domain.services.idempotency import request_hash, run_idempotent

router = APIRouter(tags=["connections"])


@router.post("/connections", status_code=201)
async def create_connection(
    body: ConnectionCreate,
    principal: PrincipalDep,
    container: ContainerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.create",
        tenant_urn(principal.tenant_id, "connection", "*"),
    )
    svc = ConnectionService(container)

    async def handler() -> tuple[int, dict[str, Any]]:
        return 201, {"data": await svc.create(principal, body)}

    status, payload, replayed = await run_idempotent(
        container.db,
        principal.tenant_id,
        idempotency_key,
        request_hash(body.model_dump()),
        handler,
    )
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(status_code=status, content=payload, headers=headers)


@router.get("/connections")
async def list_connections(
    principal: PrincipalDep,
    container: ContainerDep,
    limit: int | None = None,
    cursor: str | None = None,
    connector_type: Annotated[str | None, Query(alias="filter[connector_type]")] = None,
    traffic_direction: Annotated[str | None, Query(alias="filter[traffic_direction]")] = None,
    q: Annotated[str | None, Query(alias="filter[q]")] = None,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.read",
        tenant_urn(principal.tenant_id, "connection", "*"),
    )
    items, page = await ConnectionService(container).list(
        principal,
        connector_type=connector_type,
        traffic_direction=traffic_direction,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "page": page}


@router.post("/connections:test")
async def test_connection_adhoc(
    body: ConnectionTestAdhoc, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.execute",
        tenant_urn(principal.tenant_id, "connection", "*"),
    )
    return {"data": await ConnectionService(container).test_adhoc(principal, body)}


@router.get("/connections/{connection_id}")
async def get_connection(
    connection_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.read",
        tenant_urn(principal.tenant_id, "connection", connection_id),
    )
    return {"data": await ConnectionService(container).get(principal, connection_id)}


@router.patch("/connections/{connection_id}")
async def update_connection(
    connection_id: str,
    body: ConnectionUpdate,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.update",
        tenant_urn(principal.tenant_id, "connection", connection_id),
    )
    return {"data": await ConnectionService(container).update(principal, connection_id, body)}


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str, principal: PrincipalDep, container: ContainerDep
) -> Response:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.delete",
        tenant_urn(principal.tenant_id, "connection", connection_id),
    )
    await ConnectionService(container).delete(principal, connection_id)
    return Response(status_code=204)


@router.post("/connections/{connection_id}/test")
async def test_connection(
    connection_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.execute",
        tenant_urn(principal.tenant_id, "connection", connection_id),
    )
    return {"data": await ConnectionService(container).test_saved(principal, connection_id)}


@router.post("/connections/{connection_id}/preview")
async def preview_connection(
    connection_id: str,
    body: PreviewRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.read",
        tenant_urn(principal.tenant_id, "connection", connection_id),
    )
    return {"data": await ConnectionService(container).preview(principal, connection_id, body)}


@router.get("/connector-types")
async def list_connector_types(principal: PrincipalDep, container: ContainerDep) -> dict[str, Any]:
    """ING-FR-002 catalog. Per type: display name, category, the dynamic-form
    field schema (name/type/required/default/enum/help + secret flags derived
    from the pydantic config model + SECRET_FIELDS) and the raw JSON Schema (MCP
    facade `get_connection_schema`). The UI renders per-type forms from this."""
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.read",
        tenant_urn(principal.tenant_id, "connection", "*"),
    )
    return {"data": connector_catalog()}


@router.get("/connector-types/{connector_type}")
async def get_connector_type(
    connector_type: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.connection.read",
        tenant_urn(principal.tenant_id, "connection", "*"),
    )
    if connector_type not in CONNECTOR_TYPES:
        raise NotFoundError()
    return {"data": connector_catalog_entry(connector_type)}

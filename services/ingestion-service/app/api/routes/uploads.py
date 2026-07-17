"""Resumable upload endpoints (ING-FR-040..042).

Part bodies are consumed via `request.stream()` — the ASGI receive channel —
and forwarded chunk-by-chunk to the ObjectStore, so a part is never held in
memory (ING-FR-041).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep, PrincipalDep, tenant_urn
from app.api.schemas import UploadComplete, UploadCreate
from app.domain.policy import authorize
from app.domain.services.uploads import UploadService

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", status_code=201)
async def create_upload(
    body: UploadCreate, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.upload.create",
        tenant_urn(principal.tenant_id, "upload", "*"),
    )
    return {"data": await UploadService(container).create(principal, body)}


@router.put("/{upload_id}/parts/{n}")
async def put_part(
    upload_id: str,
    n: int,
    request: Request,
    principal: PrincipalDep,
    container: ContainerDep,
    content_sha256: Annotated[str | None, Header(alias="Content-SHA256")] = None,
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.upload.update",
        tenant_urn(principal.tenant_id, "upload", upload_id),
    )
    result = await UploadService(container).put_part(
        principal, upload_id, n, request.stream(), content_sha256
    )
    return {"data": result}


@router.get("/{upload_id}")
async def get_upload(
    upload_id: str, principal: PrincipalDep, container: ContainerDep
) -> dict[str, Any]:
    await authorize(
        container.policy,
        principal,
        "ingestion.upload.read",
        tenant_urn(principal.tenant_id, "upload", upload_id),
    )
    return {"data": await UploadService(container).get(principal, upload_id)}


@router.post("/{upload_id}/complete")
async def complete_upload(
    upload_id: str,
    body: UploadComplete,
    principal: PrincipalDep,
    container: ContainerDep,
) -> JSONResponse:
    await authorize(
        container.policy,
        principal,
        "ingestion.upload.execute",
        tenant_urn(principal.tenant_id, "upload", upload_id),
    )
    status, data = await UploadService(container).complete(principal, upload_id, body)
    return JSONResponse(status_code=status, content={"data": data})


@router.delete("/{upload_id}", status_code=204)
async def abort_upload(
    upload_id: str, principal: PrincipalDep, container: ContainerDep
) -> Response:
    await authorize(
        container.policy,
        principal,
        "ingestion.upload.delete",
        tenant_urn(principal.tenant_id, "upload", upload_id),
    )
    await UploadService(container).abort(principal, upload_id)
    return Response(status_code=204)

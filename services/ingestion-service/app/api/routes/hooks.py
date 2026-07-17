"""Webhook receive endpoint (ING-FR-024) — HMAC-authenticated, not JWT."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep
from app.domain.services.webhooks import WebhookService

router = APIRouter(prefix="/hooks", tags=["hooks"])


@router.post("/{path_token}/events")
async def receive_events(
    path_token: str,
    request: Request,
    container: ContainerDep,
    x_windrose_signature: Annotated[str | None, Header(alias="X-Windrose-Signature")] = None,
) -> JSONResponse:
    body = await request.body()  # capped at 1MB by the service (ING-FR-024)
    result: dict[str, Any] = await WebhookService(container).receive(
        path_token, body, x_windrose_signature
    )
    return JSONResponse(status_code=202, content={"data": result})

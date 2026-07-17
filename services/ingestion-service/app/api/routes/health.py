"""Health endpoints (MASTER-FR-051)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import ContainerDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(container: ContainerDep) -> Any:
    try:
        async with container.db.session_factory() as session:
            await session.execute(sa.text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": "down"})
    return {"status": "ok", "db": "up"}

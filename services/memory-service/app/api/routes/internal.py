"""Internal (mTLS/SPIFFE) endpoints: BRD 14 session-memory sanitization hook
(MEM-FR §5, AC-12). Idempotent 204."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_internal

router = APIRouter(prefix="/internal/v1")


@router.delete("/sessions/{session_id}/memory", status_code=204)
async def wipe_session(request: Request, session_id: str,
                       tenant: str, _spiffe: str = Depends(require_internal)):
    await request.app.state.container.session_service.wipe(tenant, session_id)

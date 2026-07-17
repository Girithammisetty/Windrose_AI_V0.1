"""Internal endpoints: MLflow webhook ingest (HMAC) + manual reconciliation.

Auth: the mesh sidecar injects the SPIFFE identity after mTLS; the webhook
additionally verifies a shared HMAC body signature and dedups on the delivery id
(EXP-FR-010/011). Never called by external clients.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request, Response

from app.api.auth import require_internal
from app.api.errors import error_response
from app.domain.errors import ValidationFailed
from app.utils import hmac_verify

logger = logging.getLogger(__name__)

router = APIRouter()


def _c(request: Request):
    return request.app.state.container


@router.post("/internal/mlflow/webhook")
async def mlflow_webhook(request: Request):
    c = _c(request)
    settings = c.settings
    trace_id = getattr(request.state, "trace_id", "")
    raw = await request.body()
    if len(raw) > settings.webhook_max_body_bytes:
        return error_response(413, "VALIDATION_FAILED", "webhook body too large", trace_id)
    signature = request.headers.get(settings.webhook_signature_header, "")
    if not signature or not hmac_verify(settings.webhook_hmac_secret, raw, signature):
        return error_response(401, "UNAUTHENTICATED", "bad webhook signature", trace_id)
    delivery_id = request.headers.get(settings.webhook_delivery_header)
    if not delivery_id:
        return error_response(400, "VALIDATION_FAILED", "missing delivery id", trace_id)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return error_response(400, "VALIDATION_FAILED", "invalid JSON body", trace_id)
    tenant_id = body.get("tenant_id")
    event_type = body.get("event")
    if not tenant_id or not event_type:
        return error_response(400, "VALIDATION_FAILED", "missing tenant_id/event", trace_id)

    await c.mirror_service.ingest_webhook(
        tenant_id=tenant_id, delivery_id=delivery_id, event_type=event_type, payload=body)
    # EXP-FR-011: application is async (handler stays fast). Fire-and-forget the
    # inbox applier so the mirror converges without blocking the 204.
    asyncio.create_task(_safe_apply(c, tenant_id))  # noqa: RUF006
    return Response(status_code=204)


async def _safe_apply(container, tenant_id: str) -> None:
    try:
        await container.mirror_service.apply_inbox_once(tenant_id)
    except Exception:  # noqa: BLE001
        logger.exception("inbox apply failed for tenant %s", tenant_id)


@router.post("/internal/reconcile")
async def reconcile(request: Request):
    require_internal(request)
    c = _c(request)
    trace_id = getattr(request.state, "trace_id", "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        raise ValidationFailed("tenant_id is required")
    result = await c.reconciliation_service.sweep_tenant(tenant_id)
    c.extras.setdefault("gauges", {})["mlflow_mirror_drift_total"] = result["drift_count"]
    c.extras["gauges"]["mlflow_mirror_lag_seconds"] = 0
    return {"data": result, "trace_id": trace_id}

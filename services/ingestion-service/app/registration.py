"""Deploy-time action-catalog registration (RBC-FR-022).

ingestion-service pushes its action manifest to rbac's idempotent registration
API at startup so the catalog OPA consumes (`action_known`) recognises every
action this service authorizes against. In production the call carries the
service's SPIFFE mTLS identity; in dev/e2e the service mints a short-lived
service-typed JWT signed with the platform signing key.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)

# Exact set of actions ingestion-service authorizes against (all workspace-scoped).
MANIFEST: list[str] = [
    "ingestion.connection.create",
    "ingestion.connection.read",
    "ingestion.connection.update",
    "ingestion.connection.delete",
    "ingestion.connection.execute",
    "ingestion.ingestion.create",
    "ingestion.ingestion.read",
    "ingestion.ingestion.execute",
    "ingestion.upload.create",
    "ingestion.upload.read",
    "ingestion.upload.update",
    "ingestion.upload.execute",
    "ingestion.upload.delete",
    "ingestion.schedule.create",
    "ingestion.schedule.read",
    "ingestion.schedule.update",
    "ingestion.schedule.delete",
    "ingestion.schedule.execute",
    "ingestion.writeback.create",
    "ingestion.writeback.read",
    "ingestion.writeback.approve",
    "ingestion.writeback.execute",
]


def _mint_service_token(settings) -> str:
    now = int(time.time())
    claims = {
        "sub": "svc:ingestion-service",
        "typ": "service",
        "tenant_id": settings.register_tenant_id or "00000000-0000-0000-0000-000000000000",
        "scopes": ["rbac.action.register"],
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + 300,
        "jti": f"ingestion-register-{now}",
    }
    headers = {"kid": settings.register_signing_kid} if settings.register_signing_kid else None
    return pyjwt.encode(
        claims, settings.register_signing_key_pem, algorithm="RS256", headers=headers
    )


async def register_actions(settings) -> bool:
    """Register the action manifest with rbac. Idempotent; best-effort with a
    short retry (rbac may still be starting)."""
    if not settings.rbac_url or not settings.register_signing_key_pem:
        logger.info("ingestion action registration skipped (rbac_url/signing key unset)")
        return False
    try:
        token = _mint_service_token(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingestion action registration: token mint failed: %s", exc)
        return False
    body = {"actions": [{"action": a, "workspace_scoped": True} for a in MANIFEST]}
    url = settings.rbac_url.rstrip("/") + "/api/v1/actions/register"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    last = ""
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(10):
            try:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    logger.info("ingestion action catalog registered (%d actions)", len(MANIFEST))
                    return True
                last = f"{resp.status_code} {resp.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("ingestion action registration failed: %s", last)
    return False

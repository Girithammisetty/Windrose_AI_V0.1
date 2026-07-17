"""Deploy-time action-catalog registration (RBC-FR-022).

inference-service pushes its action manifest to rbac's idempotent registration
API at startup so OPA's catalog (`action_known`) recognises every action this
service authorizes against. Best-effort with a short retry.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)

# Canonical `<service>.<resource>.<verb>` actions (MASTER-FR-016). Verbs are drawn
# from the platform-canonical set (read/list/create/update/delete/execute/…): job
# submission/retry/bulk are `create`, cancel is `update`. These names MUST equal
# the actions the routes guard (see tests/unit/test_action_manifest.py) so rbac's
# OPA catalog reports action_known=True. `inference.job.create_unpromoted` is a
# fine-grained capability (BR-2) checked in-code, not a route guard.
MANIFEST: list[str] = [
    "inference.job.create",
    "inference.job.read",
    "inference.job.update",
    "inference.job.delete",
    "inference.job.create_unpromoted",
    "inference.schedule.create",
    "inference.schedule.read",
    "inference.schedule.update",
    "inference.schedule.delete",
]


def _mint_service_token(settings) -> str:
    now = int(time.time())
    claims = {
        "sub": "svc:inference-service",
        "typ": "service",
        "tenant_id": settings.register_tenant_id or "00000000-0000-0000-0000-000000000000",
        "scopes": ["rbac.action.register"],
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + 300,
        "jti": f"inference-register-{now}",
    }
    headers = {"kid": settings.register_signing_kid} if settings.register_signing_kid else None
    return pyjwt.encode(
        claims, settings.register_signing_key_pem, algorithm="RS256", headers=headers)


async def register_actions(settings) -> bool:
    if not settings.rbac_url or not settings.register_signing_key_pem:
        logger.info("inference action registration skipped (rbac_url/signing key unset)")
        return False
    try:
        token = _mint_service_token(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inference action registration: token mint failed: %s", exc)
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
                    logger.info("inference action catalog registered (%d actions)", len(MANIFEST))
                    return True
                last = f"{resp.status_code} {resp.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("inference action registration failed: %s", last)
    return False

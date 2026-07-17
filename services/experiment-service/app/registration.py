"""Deploy-time action-catalog registration (RBC-FR-022).

experiment-service pushes its action manifest to rbac's idempotent registration
API at startup so the catalog OPA consumes (``action_known``) recognises every
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

# Exact set of actions experiment-service authorizes against. Every name is
# catalog-canonical `<service>.<resource>.<verb>` with a verb from the allowed
# set (read/list/create/update/delete/execute/assign/approve/admin/export/share)
# — "register"/"promote"/"decide" are NOT canonical verbs. These are the SAME
# strings the route guards use (asserted by tests/unit/test_action_manifest.py):
#   register a run as a model version -> experiment.model.create
#   request a stage promotion         -> experiment.model.update
#   approve/reject a promotion        -> experiment.promotion.approve
MANIFEST: list[str] = [
    "experiment.experiment.create",
    "experiment.experiment.read",
    "experiment.experiment.update",
    "experiment.experiment.delete",
    "experiment.run.read",
    "experiment.run.update",
    "experiment.run.delete",
    "experiment.model.read",
    "experiment.model.create",
    "experiment.model.update",
    "experiment.promotion.approve",
    "experiment.model_card.update",
]


def _mint_service_token(settings) -> str:
    now = int(time.time())
    claims = {
        "sub": "svc:experiment-service", "typ": "service",
        "tenant_id": settings.register_tenant_id or "00000000-0000-0000-0000-000000000000",
        "scopes": ["rbac.action.register"], "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience, "iat": now, "exp": now + 300,
        "jti": f"experiment-register-{now}",
    }
    headers = {"kid": settings.register_signing_kid} if settings.register_signing_kid else None
    return pyjwt.encode(claims, settings.register_signing_key_pem, algorithm="RS256",
                        headers=headers)


async def register_actions(settings) -> bool:
    if not settings.rbac_url or not settings.register_signing_key_pem:
        logger.info("experiment action registration skipped (rbac_url/signing key unset)")
        return False
    try:
        token = _mint_service_token(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("experiment action registration: token mint failed: %s", exc)
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
                    logger.info("experiment action catalog registered (%d actions)",
                                len(MANIFEST))
                    return True
                last = f"{resp.status_code} {resp.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("experiment action registration failed: %s", last)
    return False

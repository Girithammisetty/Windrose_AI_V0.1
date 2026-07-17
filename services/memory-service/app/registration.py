"""Deploy-time action-catalog registration (RBC-FR-022).

memory-service pushes its action manifest to rbac's idempotent registration
API at startup so the OPA catalog (`action_known`) recognises every action
this service authorizes against."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)

# Canonical rbac action names (`<service>.<resource>.<verb>`; verbs limited to
# the catalog set read/list/create/update/delete/execute/assign/approve/admin/
# export/share). This MUST equal the set of actions the routes guard.
# Semantics: memory.read = retrieve/browse/get; memory.create = write/batch;
# memory.update = edit + unquarantine; corpus.admin = corpus lifecycle + doc
# push; policy read/update = tenant policy; erasure create/read = GDPR erasure.
MANIFEST: list[str] = [
    "memory.memory.read",
    "memory.memory.create",
    "memory.memory.update",
    "memory.memory.delete",
    "memory.corpus.admin",
    "memory.policy.read",
    "memory.policy.update",
    "memory.erasure.create",
    "memory.erasure.read",
    "memory.stats.read",
]


def _mint_service_token(settings) -> str:
    now = int(time.time())
    claims = {
        "sub": "svc:memory-service", "typ": "service",
        "tenant_id": settings.register_tenant_id or "00000000-0000-0000-0000-000000000000",
        "scopes": ["rbac.action.register"], "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience, "iat": now, "exp": now + 300,
        "jti": f"memory-register-{now}"}
    headers = {"kid": settings.register_signing_kid} if settings.register_signing_kid else None
    return pyjwt.encode(claims, settings.register_signing_key_pem, algorithm="RS256",
                        headers=headers)


async def register_actions(settings) -> bool:
    if not settings.rbac_url or not settings.register_signing_key_pem:
        logger.info("memory action registration skipped (rbac_url/signing key unset)")
        return False
    try:
        token = _mint_service_token(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory action registration: token mint failed: %s", exc)
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
                    logger.info("memory action catalog registered (%d actions)",
                                len(MANIFEST))
                    return True
                last = f"{resp.status_code} {resp.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("memory action registration failed: %s", last)
    return False

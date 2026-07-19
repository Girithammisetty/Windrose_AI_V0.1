"""Deploy-time action-catalog registration (RBC-FR-022).

dataset-service pushes its action manifest to rbac's idempotent registration
API at startup so the catalog OPA consumes (`action_known`) recognises every
action this service authorizes against. In production the call carries the
service's SPIFFE mTLS identity; in dev/e2e the service mints a short-lived
service-typed JWT signed with the platform signing key.
"""

from __future__ import annotations

import logging
import time

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)

# Exact set of actions dataset-service authorizes against (all workspace-scoped).
MANIFEST: list[str] = [
    "dataset.dataset.create",
    "dataset.dataset.read",
    "dataset.dataset.update",
    "dataset.dataset.delete",
    "dataset.profile.execute",
    "dataset.profile.read",
    "dataset.lineage.update",
    "dataset.lineage.read",
    # BRD 56: run entity resolution over a dataset (produces a link/view layer).
    "dataset.entity.execute",
    # BRD 56 inc2: read resolved-entity runs/clusters/candidates; confirm a
    # below-auto merge (four-eyes proposal execution federates to the facade).
    "dataset.entity.read",
    "dataset.entity.merge",
    # inc11: governed domain ontology (entity-type registry) a pack declares.
    "dataset.ontology.read",
    "dataset.ontology.list",
    "dataset.ontology.create",
    "dataset.ontology.delete",
]


def _mint_service_token(settings) -> str:
    now = int(time.time())
    claims = {
        "sub": "svc:dataset-service",
        "typ": "service",
        "tenant_id": settings.register_tenant_id or "00000000-0000-0000-0000-000000000000",
        "scopes": ["rbac.action.register"],
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + 300,
        "jti": f"dataset-register-{now}",
    }
    headers = {"kid": settings.register_signing_kid} if settings.register_signing_kid else None
    return pyjwt.encode(
        claims, settings.register_signing_key_pem, algorithm="RS256", headers=headers
    )


async def register_actions(settings) -> bool:
    """Register the action manifest with rbac. Idempotent; best-effort with a
    short retry (rbac may still be starting). Returns True on success."""
    if not settings.rbac_url or not settings.register_signing_key_pem:
        logger.info("dataset action registration skipped (rbac_url/signing key unset)")
        return False
    try:
        token = _mint_service_token(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dataset action registration: token mint failed: %s", exc)
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
                    logger.info("dataset action catalog registered (%d actions)", len(MANIFEST))
                    return True
                last = f"{resp.status_code} {resp.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            import asyncio

            await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("dataset action registration failed: %s", last)
    return False

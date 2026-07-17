"""Per-tenant ai-gateway virtual-key provider (ART-FR-012).

agent-runtime is ONE shared process serving every tenant in the platform. A
single fixed ``AR_AI_GATEWAY_VIRTUAL_KEY`` (env-var override, dev-only) is
scoped to exactly one tenant — ai-gateway's middleware rejects any OTHER
tenant's call with that key as a tenant/key mismatch ("virtual key is invalid
or revoked"), indistinguishable from a genuinely bad key. Any other tenant's
agent activity interleaving on this shared process intermittently 401s.

This provider mints a REAL, short-lived, tenant-scoped virtual key via
ai-gateway's own admin API (``POST /v1/keys``, authenticated with
agent-runtime's own self-signed agent_autonomous JWT for that tenant — the
same mechanism already used for OBO/tool-plane calls), caching it per tenant
for most of its TTL so a busy tenant doesn't mint a fresh key on every single
LLM call. This is the "minted per run" design the settings field
(``ai_gateway_virtual_key: str | None = None  # nk-... minted per-run in
prod``) already documents as the intended production behavior.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger("agent-runtime.vkeys")

_TTL_SECONDS = 3600  # key lifetime at ai-gateway
_REFRESH_MARGIN_SECONDS = 300  # mint a replacement 5 min before expiry


class TenantVirtualKeyProvider:
    """Mints + caches one ai-gateway virtual key per tenant.

    ``jwt_provider(tenant_id)`` must return a JWT ai-gateway's admin API will
    accept for ``ai.key.write`` on that tenant (agent-runtime's own
    agent_autonomous self-signed token, scopes=["*"], satisfies this).
    """

    def __init__(
        self,
        ai_gateway_url: str,
        *,
        jwt_provider,  # callable(tenant_id) -> jwt str
        principal_id: str = "agent-runtime@1",
        allowed_request_classes: list[str] | None = None,
        max_rung: int = 3,
        timeout_s: float = 10.0,
    ) -> None:
        self._base = ai_gateway_url.rstrip("/")
        self._jwt_provider = jwt_provider
        self._principal_id = principal_id
        self._allowed_request_classes = allowed_request_classes or ["chat"]
        self._max_rung = max_rung
        self._timeout = timeout_s
        self._cache: dict[str, tuple[str, float]] = {}  # tenant_id -> (secret, expires_at)
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, tenant_id: str) -> str:
        cached = self._cache.get(tenant_id)
        now = time.time()
        if cached and now < cached[1] - _REFRESH_MARGIN_SECONDS:
            return cached[0]
        # One mint in flight per tenant even under concurrent callers.
        lock = self._locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            cached = self._cache.get(tenant_id)
            now = time.time()
            if cached and now < cached[1] - _REFRESH_MARGIN_SECONDS:
                return cached[0]
            secret = await self._mint(tenant_id)
            self._cache[tenant_id] = (secret, now + _TTL_SECONDS)
            return secret

    async def _mint(self, tenant_id: str) -> str:
        jwt = self._jwt_provider(tenant_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/api/v1/admin/keys",
                headers={"authorization": f"Bearer {jwt}", "content-type": "application/json"},
                json={
                    "principal_type": "agent",
                    "principal_id": self._principal_id,
                    "allowed_request_classes": self._allowed_request_classes,
                    "max_rung": self._max_rung,
                    "ttl_seconds": _TTL_SECONDS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        secret = (data.get("data") or data).get("secret")
        if not secret:
            raise RuntimeError("ai-gateway key mint returned no secret")
        logger.info("minted tenant-scoped ai-gateway virtual key: tenant=%s", tenant_id)
        return secret

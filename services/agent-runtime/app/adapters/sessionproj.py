"""Chat-session ownership projection for realtime-hub authz (RTH-FR-003).

The hub authorizes ``chat:<session_id>`` topic subscriptions by reading the
Redis key ``rt:session:{tenant}/{session_id}`` -> owner user sub
(services/realtime-hub/internal/authz/opa.go redisSessions). agent-runtime is
the writer of that projection: it writes/refreshes the key whenever a session
is created or resumed, with TTL = the session's remaining hard lifetime.

Best-effort: a Redis blip must not fail the chat turn, but is logged loudly so
a silently-broken hub authz is visible.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("agent-runtime.sessionproj")


def session_key(tenant_id: str, session_id: str) -> str:
    return f"rt:session:{tenant_id}/{session_id}"


class RedisSessionProjection:
    def __init__(self, redis_url: str) -> None:
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)

    async def put(self, *, tenant_id: str, session_id: str, owner_sub: str,
                  ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        try:
            await self._redis.set(session_key(tenant_id, session_id), owner_sub,
                                  ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001 — non-fatal, but never silent
            logger.warning(
                "rt:session projection write failed (hub chat authz degraded): "
                "session=%s err=%r", session_id, exc)

    async def aclose(self) -> None:
        await self._redis.aclose()


class InMemorySessionProjection:
    """Unit-tier double. Never wired from app.main."""

    def __init__(self) -> None:
        self.keys: dict[str, tuple[str, int]] = {}

    async def put(self, *, tenant_id: str, session_id: str, owner_sub: str,
                  ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        self.keys[session_key(tenant_id, session_id)] = (owner_sub, ttl_seconds)

"""Virtual key lifecycle + authentication (AIG-FR-030..032)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from app.config import REQUEST_CLASSES, Settings
from app.domain.entities import VirtualKey
from app.domain.errors import KeyInvalid, NotFound, ValidationFailed
from app.domain.ports import InvalidationChannel, UowFactory
from app.utils import Clock, sha256_hex, uuid7

_CACHE_TTL_SECONDS = 30  # revocation must take effect ≤ 30s (AIG-FR-031)


class KeyService:
    def __init__(self, uow_factory: UowFactory, clock: Clock, settings: Settings,
                 invalidation: InvalidationChannel):
        self.uow_factory = uow_factory
        self.clock = clock
        self.settings = settings
        self.invalidation = invalidation
        # key_hash -> (VirtualKey, cached_at). Pub/sub invalidation clears
        # entries immediately; TTL bounds staleness anyway.
        self._cache: dict[str, tuple[VirtualKey, datetime]] = {}
        invalidation.subscribe(self._on_invalidate)

    async def _on_invalidate(self, kind: str, ref: str) -> None:
        if kind == "key":
            self._cache = {h: v for h, v in self._cache.items() if v[0].id != ref}
        elif kind == "tenant_keys":
            self._cache = {h: v for h, v in self._cache.items() if v[0].tenant_id != ref}

    # ------------------------------------------------------------------- lifecycle

    async def create(self, tenant_id: str, *, principal_type: str, principal_id: str,
                     allowed_request_classes: list[str] | None, max_rung: int,
                     ttl_seconds: int | None = None,
                     expires_at: datetime | None = None) -> tuple[VirtualKey, str]:
        classes = allowed_request_classes or list(REQUEST_CLASSES)
        bad = [c for c in classes if c not in REQUEST_CLASSES]
        if bad:
            raise ValidationFailed(f"unknown request classes: {bad}")
        if principal_type not in ("user", "agent", "service"):
            raise ValidationFailed("principal_type must be user|agent|service")
        if ttl_seconds is not None:
            expires_at = self.clock.now() + timedelta(seconds=ttl_seconds)
        secret = f"nk-{secrets.token_urlsafe(32)}"
        key = VirtualKey(
            id=str(uuid7()),
            tenant_id=tenant_id,
            key_hash=sha256_hex(secret),
            principal_type=principal_type,
            principal_id=principal_id,
            allowed_request_classes=classes,
            max_rung=max_rung,
            expires_at=expires_at,
            status="active",
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        async with self.uow_factory(tenant_id) as uow:
            await uow.keys.add(key)
            await uow.outbox.add(self.settings.events_topic, _key_event(
                "key.created", key, self.clock.now()))
            await uow.commit()
        return key, secret  # secret shown once (AIG-FR-030)

    async def rotate(self, tenant_id: str, key_id: str) -> tuple[VirtualKey, str]:
        secret = f"nk-{secrets.token_urlsafe(32)}"
        async with self.uow_factory(tenant_id) as uow:
            key = await uow.keys.get(key_id)
            if key is None or key.status != "active":
                raise NotFound("virtual key not found")
            key.key_hash = sha256_hex(secret)
            key.updated_at = self.clock.now()
            await uow.keys.update(key)
            await uow.commit()
        await self.invalidation.publish("key", key_id)
        return key, secret

    async def revoke(self, tenant_id: str, key_id: str) -> VirtualKey:
        async with self.uow_factory(tenant_id) as uow:
            key = await uow.keys.get(key_id)
            if key is None:
                raise NotFound("virtual key not found")
            key.status = "revoked"
            key.updated_at = self.clock.now()
            await uow.keys.update(key)
            await uow.outbox.add(self.settings.events_topic, _key_event(
                "key.revoked", key, self.clock.now()))
            await uow.commit()
        await self.invalidation.publish("key", key_id)
        return key

    async def revoke_all_for_tenant(self, tenant_id: str) -> int:
        """tenant.suspended → disable all tenant keys ≤ 30s (§6, BR-18)."""
        count = 0
        async with self.uow_factory(tenant_id) as uow:
            for key in await uow.keys.list_active():
                key.status = "revoked"
                key.updated_at = self.clock.now()
                await uow.keys.update(key)
                count += 1
            await uow.commit()
        await self.invalidation.publish("tenant_keys", tenant_id)
        return count

    # ---------------------------------------------------------------- authentication

    async def authenticate(self, secret: str) -> VirtualKey:
        if not secret.startswith("nk-"):
            raise KeyInvalid("malformed virtual key")
        key_hash = sha256_hex(secret)
        now = self.clock.now()
        cached = self._cache.get(key_hash)
        if cached and (now - cached[1]).total_seconds() < _CACHE_TTL_SECONDS:
            key = cached[0]
        else:
            # lookup crosses tenants by hash: keys authenticate before the
            # tenant is known; the JWT tenant must then match the key's.
            async with self.uow_factory("") as uow:
                key = await uow.keys.get_by_hash_any_tenant(key_hash)
            if key is not None and key.status == "active":
                self._cache[key_hash] = (key, now)
        if key is None or key.status != "active":
            raise KeyInvalid("virtual key is invalid or revoked")
        if key.expires_at is not None and now >= key.expires_at:
            raise KeyInvalid("virtual key has expired")
        return key


def _key_event(event_type: str, key: VirtualKey, occurred_at) -> dict:
    from app.events.envelope import make_envelope

    return make_envelope(
        event_type=event_type,
        tenant_id=key.tenant_id,
        actor={"type": "service", "id": "ai-gateway"},
        resource_urn=f"wr:{key.tenant_id}:ai:virtual_key/{key.id}",
        payload={"key_id": key.id, "principal_type": key.principal_type,
                 "principal_id": key.principal_id},
    )

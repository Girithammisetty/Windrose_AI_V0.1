"""Per-tenant admission control (AIG-FR-011, BR-13): concurrent-stream cap +
fixed-window RPM/TPM counters. Over-cap → 429 + Retry-After, no queuing."""

from __future__ import annotations

from app.config import Settings
from app.domain.errors import RateLimited
from app.domain.ports import KV
from app.utils import Clock


class AdmissionController:
    def __init__(self, kv: KV, clock: Clock, settings: Settings):
        self.kv = kv
        self.clock = clock
        self.settings = settings

    def _retry_after(self) -> int:
        return max(1, 60 - int(self.clock.now().timestamp()) % 60)

    async def check_rpm_tpm(self, tenant_id: str, tokens: int) -> None:
        minute = int(self.clock.now().timestamp()) // 60
        rpm = await self.kv.incr(f"adm:{tenant_id}:rpm:{minute}", ttl_seconds=120)
        if rpm > self.settings.rpm_cap_per_tenant:
            raise RateLimited("tenant RPM cap exceeded", retry_after=self._retry_after())
        tpm = await self.kv.incrby(f"adm:{tenant_id}:tpm:{minute}", tokens, ttl_seconds=120)
        if tpm > self.settings.tpm_cap_per_tenant:
            raise RateLimited("tenant TPM cap exceeded", retry_after=self._retry_after())

    async def acquire_stream(self, tenant_id: str) -> None:
        gauge = await self.kv.incr(f"adm:{tenant_id}:streams")
        if gauge > self.settings.streams_cap_per_tenant:
            await self.kv.decr(f"adm:{tenant_id}:streams")
            raise RateLimited(
                "tenant concurrent-stream cap exceeded", retry_after=self._retry_after()
            )

    async def release_stream(self, tenant_id: str) -> None:
        current = await self.kv.decr(f"adm:{tenant_id}:streams")
        if current < 0:  # defensive: never go negative
            await self.kv.incr(f"adm:{tenant_id}:streams")

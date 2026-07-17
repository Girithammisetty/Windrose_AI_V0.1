"""Provider registry admin operations + deployment state machine
(AIG-FR-003, §4 state machines) and active health probing (AIG-FR-009a)."""

from __future__ import annotations

from app.config import Settings
from app.domain.entities import (
    CLOUDS,
    DEPLOYMENT_STATUSES,
    PROVIDERS,
    ProviderDeployment,
)
from app.domain.errors import Conflict, NotFound, ValidationFailed
from app.domain.ports import ProviderClient, ProviderRequest, UowFactory
from app.domain.routing import HealthRegistry
from app.events.envelope import make_envelope
from app.utils import Clock, uuid7

_TRANSITIONS = {
    ("active", "draining"), ("draining", "disabled"),
    ("disabled", "active"), ("active", "disabled"),
}


class ProviderAdminService:
    def __init__(self, uow_factory: UowFactory, settings: Settings, clock: Clock):
        self.uow_factory = uow_factory
        self.settings = settings
        self.clock = clock

    def _uow(self):
        return self.uow_factory(self.settings.platform_tenant_id)

    async def create(self, fields: dict) -> ProviderDeployment:
        if fields.get("provider") not in PROVIDERS:
            raise ValidationFailed(f"provider must be one of {PROVIDERS}")
        if fields.get("cloud") not in CLOUDS:
            raise ValidationFailed(f"cloud must be one of {CLOUDS}")
        now = self.clock.now()
        deployment = ProviderDeployment(
            id=str(uuid7()),
            tenant_id=self.settings.platform_tenant_id,
            provider=fields["provider"],
            model_family=fields["model_family"],
            deployment_name=fields["deployment_name"],
            region=fields["region"],
            cloud=fields["cloud"],
            endpoint_vault_ref=fields["endpoint_vault_ref"],
            tpm_limit=int(fields.get("tpm_limit", 0)),
            rpm_limit=int(fields.get("rpm_limit", 0)),
            priority=int(fields.get("priority", 100)),
            status="active",
            created_at=now,
            updated_at=now,
        )
        async with self._uow() as uow:
            await uow.providers.add(deployment)
            await uow.commit()
        return deployment

    async def get(self, deployment_id: str) -> ProviderDeployment:
        async with self._uow() as uow:
            d = await uow.providers.get(deployment_id)
        if d is None:
            raise NotFound("provider deployment not found")
        return d

    async def patch(self, deployment_id: str, fields: dict,
                    force: bool = False) -> ProviderDeployment:
        async with self._uow() as uow:
            d = await uow.providers.get(deployment_id)
            if d is None:
                raise NotFound("provider deployment not found")
            new_status = fields.get("status")
            if new_status and new_status != d.status:
                if new_status not in DEPLOYMENT_STATUSES:
                    raise ValidationFailed(f"status must be one of {DEPLOYMENT_STATUSES}")
                if (d.status, new_status) not in _TRANSITIONS:
                    raise Conflict(f"illegal transition {d.status} → {new_status}")
                if new_status in ("draining", "disabled") and not force:
                    await self._guard_last_deployment(uow, d)
                await uow.outbox.add(self.settings.events_topic, make_envelope(
                    event_type="provider.state_changed",
                    tenant_id=self.settings.platform_tenant_id,
                    actor={"type": "service", "id": "ai-gateway"},
                    resource_urn=(
                        f"wr:{self.settings.platform_tenant_id}:ai:provider/{d.id}"
                    ),
                    payload={"deployment_id": d.id, "from": d.status,
                             "to": new_status, "reason": fields.get("reason", "admin")},
                ))
                d.status = new_status
            for f in ("priority", "tpm_limit", "rpm_limit", "endpoint_vault_ref"):
                if f in fields and fields[f] is not None:
                    setattr(d, f, fields[f])
            d.updated_at = self.clock.now()
            await uow.providers.update(d)
            await uow.commit()
        return d

    async def drain(self, deployment_id: str, force: bool = False) -> ProviderDeployment:
        """Drain finishes in-flight requests and accepts none (AIG-FR-070)."""
        return await self.patch(deployment_id, {"status": "draining",
                                                "reason": "drain"}, force=force)

    async def _guard_last_deployment(self, uow, d: ProviderDeployment) -> None:
        """Cannot disable/drain the last active deployment of a rung alias
        without force=true (§4 state machine guard)."""
        remaining = await uow.providers.count_active_for_alias(
            d.model_family, exclude_id=d.id
        )
        if remaining == 0:
            raise Conflict(
                f"deployment {d.id} is the last active deployment serving "
                f"{d.model_family!r}; pass force=true to proceed"
            )

    async def list(self, limit: int, cursor: str | None):
        async with self._uow() as uow:
            return await uow.providers.list(limit, cursor)


class HealthProber:
    """60s-interval synthetic 1-token probe per active deployment
    (AIG-FR-009a). `probe_once` is invoked by the scheduler loop in main."""

    def __init__(self, uow_factory: UowFactory, settings: Settings,
                 provider: ProviderClient, health: HealthRegistry):
        self.uow_factory = uow_factory
        self.settings = settings
        self.provider = provider
        self.health = health

    async def probe_once(self) -> dict[str, bool]:
        async with self.uow_factory(self.settings.platform_tenant_id) as uow:
            deployments = await uow.providers.list_all_active_or_draining()
        results: dict[str, bool] = {}
        for d in deployments:
            if d.status != "active":
                continue
            try:
                await self.provider.complete(d, ProviderRequest(
                    model=d.deployment_name,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    temperature=0.0,
                ))
                ok = True
            except Exception:  # noqa: BLE001 - any probe failure marks unhealthy
                ok = False
            self.health.record_probe(d.id, ok)
            results[d.id] = ok
        return results

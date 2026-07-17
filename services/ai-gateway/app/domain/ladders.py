"""Model ladders: resolution, escalation, degradation (AIG-FR-005/006/007)."""

from __future__ import annotations

from app.config import DEFAULT_LADDERS, Settings
from app.domain.entities import ModelLadder
from app.domain.errors import LadderCap, ValidationFailed
from app.domain.ports import UowFactory
from app.utils import uuid7


class LadderService:
    def __init__(self, uow_factory: UowFactory, settings: Settings):
        self.uow_factory = uow_factory
        self.settings = settings

    async def resolve(self, tenant_id: str, request_class: str) -> ModelLadder:
        """Tenant override wins; otherwise platform row; otherwise built-in
        defaults (AIG-FR-005)."""
        async with self.uow_factory(tenant_id) as uow:
            ladder = await uow.ladders.get(request_class, "tenant")
        if ladder is not None:
            return ladder
        async with self.uow_factory(self.settings.platform_tenant_id) as uow:
            ladder = await uow.ladders.get(request_class, "platform")
        if ladder is not None:
            return ladder
        return ModelLadder(
            id=f"default-{request_class}",
            tenant_id=self.settings.platform_tenant_id,
            request_class=request_class,
            scope="platform",
            rungs=DEFAULT_LADDERS[request_class],
        )

    def select_rung(self, ladder: ModelLadder, *, requested_model: str,
                    min_rung: int | None, escalate_from: int | None,
                    key_max_rung: int, degraded: bool) -> tuple[int, bool]:
        """Returns (rung_index, escalated). Degradation forces the lowest rung
        and denies escalation (AIG-FR-007)."""
        if degraded:
            if escalate_from is not None:
                raise LadderCap("escalation denied while budget-degraded")
            return 0, False

        escalated = False
        if escalate_from is not None:
            rung = escalate_from + 1
            escalated = True
        elif requested_model == "windrose-auto":
            rung = min_rung or 0
        else:
            aliases = [r["model_alias"] for r in ladder.rungs]
            if requested_model not in aliases:
                raise ValidationFailed(
                    f"model {requested_model!r} is not a rung of the "
                    f"{ladder.request_class} ladder",
                    details=[{"field": "model", "problem": f"allowed: {aliases}"}],
                )
            rung = aliases.index(requested_model)
            if min_rung is not None:
                rung = max(rung, min_rung)

        cap = ladder.top_rung
        if ladder.max_rung is not None:
            cap = min(cap, ladder.max_rung)
        cap = min(cap, key_max_rung)
        if rung > cap:
            raise LadderCap(
                f"rung {rung} exceeds the allowed maximum rung {cap}",
                details={"requested_rung": rung, "max_rung": cap},
            )
        return rung, escalated

    @staticmethod
    def validate_rungs(rungs: list[dict]) -> None:
        if not rungs or len(rungs) > 8:
            raise ValidationFailed("a ladder needs 1–8 rungs")
        for i, r in enumerate(rungs):
            for field in ("model_alias", "max_tokens", "temperature_default", "cost_tier"):
                if field not in r:
                    raise ValidationFailed(
                        "invalid rung",
                        details=[{"field": f"rungs.{i}.{field}", "problem": "required"}],
                    )

    async def put(self, tenant_id: str, request_class: str, scope: str,
                  rungs: list[dict], max_rung: int | None = None) -> ModelLadder:
        self.validate_rungs(rungs)
        owner = self.settings.platform_tenant_id if scope == "platform" else tenant_id
        async with self.uow_factory(owner) as uow:
            existing = await uow.ladders.get(request_class, scope)
            ladder = ModelLadder(
                id=existing.id if existing else str(uuid7()),
                tenant_id=owner,
                request_class=request_class,
                scope=scope,
                rungs=rungs,
                version=(existing.version + 1) if existing else 1,
                max_rung=max_rung,
            )
            ladder = await uow.ladders.upsert(ladder)
            await uow.commit()
        return ladder

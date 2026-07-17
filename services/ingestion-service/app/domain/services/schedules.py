"""Schedules (ING-FR-060..064, BR-10, AC-8/9)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from croniter import croniter

from app.api.auth import Principal
from app.api.schemas import ScheduleCreate, ScheduleUpdate
from app.container import Container
from app.domain.errors import ConflictError, ValidationFailedError
from app.domain.services.common import iso, raise_not_found_with_audit
from app.domain.services.transitions import ingestion_urn, record_transition
from app.domain.state_machine import TERMINAL_STATUSES, TransitionContext
from app.domain.watermark import WatermarkSpec, validate_spec
from app.events.outbox import emit_event
from app.ids import uuid7
from app.store.models import Connection, Ingestion, Schedule


def serialize_schedule(sched: Schedule, next_fire_at: datetime | None = None) -> dict[str, Any]:
    watermark = None
    if sched.watermark_column:
        watermark = {
            "column": sched.watermark_column,
            "operator": sched.watermark_operator,
            "value_type": sched.watermark_value_type,
            "current_value": sched.watermark_value,
        }
    return {
        "id": sched.id,
        "connection_id": sched.connection_id,
        "ingestion_template": sched.ingestion_template,
        "cron": sched.cron,
        "interval_seconds": sched.interval_seconds,
        "timezone": sched.timezone,
        "watermark": watermark,
        "overlap_policy": sched.overlap_policy,
        "enabled": sched.enabled,
        "temporal_schedule_id": sched.temporal_schedule_id,
        "workspace_id": sched.workspace_id,
        "last_fired_at": iso(sched.last_fired_at),
        "next_fire_at": iso(next_fire_at) if next_fire_at else None,
        "created_at": iso(sched.created_at),
        "updated_at": iso(sched.updated_at),
    }


class ScheduleService:
    def __init__(self, container: Container) -> None:
        self.c = container
        # in-process scheduler needs tenant lookup per fire (Temporal carries it natively)
        if not container.scheduler_bound:
            container.scheduler.bind(self._on_fire)
            container.scheduler_bound = True

    def _tenants(self) -> dict[str, str]:
        return self.c.schedule_tenants

    async def _on_fire(self, schedule_id: str) -> None:
        tenant_id = self._tenants().get(schedule_id)
        if tenant_id:
            await self.fire(tenant_id, schedule_id)

    # -------------------------------------------------------------- validate
    def _validate_timing(
        self, cron: str | None, interval_seconds: int | None, timezone: str
    ) -> None:
        details = []
        if bool(cron) == bool(interval_seconds):
            details.append(
                {"field": "cron", "message": "provide exactly one of cron / interval_seconds"}
            )
        if cron and not croniter.is_valid(cron):
            details.append({"field": "cron", "message": "invalid cron expression"})
        try:
            ZoneInfo(timezone)
        except Exception:
            details.append({"field": "timezone", "message": "unknown IANA timezone"})
        if details:
            raise ValidationFailedError("invalid schedule timing", details=details)

    def _validate_template(self, template: dict[str, Any]) -> None:
        details = []
        mode = template.get("ingestion_mode")
        if mode == "file_poll":
            # ING-FR-064 (Should) — stubbed per definition-of-done
            raise ValidationFailedError(
                "file_poll schedules are not implemented yet (TODO ING-FR-064)",
                details=[
                    {"field": "ingestion_template.ingestion_mode", "message": "file_poll TODO"}
                ],
            )
        if mode != "query":
            details.append(
                {"field": "ingestion_template.ingestion_mode", "message": "must be 'query'"}
            )
        if not template.get("statement"):
            details.append({"field": "ingestion_template.statement", "message": "required"})
        if not template.get("dataset_urn") and not template.get("new_dataset"):
            details.append(
                {"field": "ingestion_template.dataset_urn", "message": "dataset target required"}
            )
        if details:
            raise ValidationFailedError("invalid ingestion_template", details=details)

    # ---------------------------------------------------------------- create
    async def create(self, principal: Principal, body: ScheduleCreate) -> dict[str, Any]:
        self._validate_timing(body.cron, body.interval_seconds, body.timezone)
        self._validate_template(body.ingestion_template)
        if body.watermark:
            validate_spec(
                WatermarkSpec(
                    column=body.watermark.column,
                    operator=body.watermark.operator,
                    value_type=body.watermark.value_type,
                    value=body.watermark.initial_value,
                )
            )
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            conn = (
                await session.execute(
                    sa.select(Connection).where(
                        Connection.id == body.connection_id,
                        Connection.tenant_id == principal.tenant_id,
                        Connection.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if conn is None:
                await raise_not_found_with_audit(
                    session, principal, Connection, body.connection_id, "connection"
                )
            schedule_id = uuid7()
            temporal_id = await self.c.scheduler.register(
                schedule_id,
                cron=body.cron,
                interval_seconds=body.interval_seconds,
                timezone=body.timezone,
            )
            self._tenants()[schedule_id] = principal.tenant_id
            sched = Schedule(
                id=schedule_id,
                tenant_id=principal.tenant_id,
                workspace_id=body.workspace_id,
                connection_id=body.connection_id,
                ingestion_template=body.ingestion_template,
                cron=body.cron,
                interval_seconds=body.interval_seconds,
                timezone=body.timezone,
                watermark_column=body.watermark.column if body.watermark else None,
                watermark_operator=body.watermark.operator if body.watermark else ">",
                watermark_value_type=body.watermark.value_type if body.watermark else "string",
                watermark_value=body.watermark.initial_value if body.watermark else None,
                overlap_policy=body.overlap_policy,
                enabled=body.enabled,
                temporal_schedule_id=temporal_id,
                created_by=principal.sub,
            )
            if not body.enabled:
                await self.c.scheduler.pause(schedule_id)
            session.add(sched)
            emit_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="schedule.created",
                resource_urn=f"wr:{principal.tenant_id}:ingestion:schedule/{schedule_id}",
                payload={"schedule_id": schedule_id, "connection_id": body.connection_id},
                actor=principal.actor(),
                via_agent=principal.via_agent(),
            )
            await session.commit()
            return serialize_schedule(sched, self.c.scheduler.next_fire_at(schedule_id))

    async def _get(self, session, principal: Principal, schedule_id: str) -> Schedule:
        sched = (
            await session.execute(
                sa.select(Schedule).where(
                    Schedule.id == schedule_id,
                    Schedule.tenant_id == principal.tenant_id,
                    Schedule.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if sched is None:
            await raise_not_found_with_audit(session, principal, Schedule, schedule_id, "schedule")
        return sched

    async def get(self, principal: Principal, schedule_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            sched = await self._get(session, principal, schedule_id)
            return serialize_schedule(sched, self.c.scheduler.next_fire_at(schedule_id))

    async def list(
        self, principal: Principal, *, limit: int | None, cursor: str | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from app.api.pagination import paginate

        stmt = sa.select(Schedule).where(
            Schedule.tenant_id == principal.tenant_id, Schedule.deleted_at.is_(None)
        )
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            items, page = await paginate(session, stmt, Schedule.id, limit=limit, cursor=cursor)
            return [serialize_schedule(s, self.c.scheduler.next_fire_at(s.id)) for s in items], page

    async def update(
        self, principal: Principal, schedule_id: str, body: ScheduleUpdate
    ) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            sched = await self._get(session, principal, schedule_id)
            cron = body.cron if body.cron is not None else sched.cron
            interval = (
                body.interval_seconds
                if body.interval_seconds is not None
                else sched.interval_seconds
            )
            timezone = body.timezone if body.timezone is not None else sched.timezone
            if body.cron is not None and body.interval_seconds is None:
                interval = None
            if body.interval_seconds is not None and body.cron is None:
                cron = None
            self._validate_timing(cron, interval, timezone)
            if body.ingestion_template is not None:
                self._validate_template(body.ingestion_template)
                sched.ingestion_template = body.ingestion_template
            sched.cron, sched.interval_seconds, sched.timezone = cron, interval, timezone
            if body.overlap_policy is not None:
                sched.overlap_policy = body.overlap_policy
            if body.enabled is not None:
                sched.enabled = body.enabled
            await self.c.scheduler.register(
                schedule_id, cron=cron, interval_seconds=interval, timezone=timezone
            )
            self._tenants()[schedule_id] = principal.tenant_id
            if not sched.enabled:
                await self.c.scheduler.pause(schedule_id)
            await session.commit()
            return serialize_schedule(sched, self.c.scheduler.next_fire_at(schedule_id))

    async def delete(self, principal: Principal, schedule_id: str) -> None:
        """BR-12: deleting a schedule never deletes past ingestions or data."""
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            sched = await self._get(session, principal, schedule_id)
            sched.deleted_at = datetime.now(UTC)
            sched.enabled = False
            await session.commit()
        await self.c.scheduler.unregister(schedule_id)
        self._tenants().pop(schedule_id, None)

    async def pause(self, principal: Principal, schedule_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            sched = await self._get(session, principal, schedule_id)
            sched.enabled = False
            await session.commit()
        await self.c.scheduler.pause(schedule_id)
        return await self.get(principal, schedule_id)

    async def resume(self, principal: Principal, schedule_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            sched = await self._get(session, principal, schedule_id)
            sched.enabled = True
            await session.commit()
        await self.c.scheduler.resume(schedule_id)
        return await self.get(principal, schedule_id)

    async def run_now(self, principal: Principal, schedule_id: str) -> dict[str, Any]:
        async with self.c.db.tenant_session(principal.tenant_id) as session:
            await self._get(session, principal, schedule_id)  # 404 check
        result = await self.fire(principal.tenant_id, schedule_id, force=True)
        if result is None:
            raise ConflictError("schedule is disabled or deleted")
        return result

    # ------------------------------------------------------------------ fire
    async def fire(
        self, tenant_id: str, schedule_id: str, *, force: bool = False
    ) -> dict[str, Any] | None:
        """ING-FR-062 + BR-10: one job per fire, honoring overlap_policy."""
        async with self.c.db.tenant_session(tenant_id) as session:
            sched = (
                await session.execute(
                    sa.select(Schedule).where(
                        Schedule.id == schedule_id,
                        Schedule.tenant_id == tenant_id,
                        Schedule.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if sched is None or (not sched.enabled and not force):
                return None

            active = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Ingestion)
                    .where(
                        Ingestion.tenant_id == tenant_id,
                        Ingestion.schedule_id == schedule_id,
                        Ingestion.status.notin_(TERMINAL_STATUSES),
                    )
                )
            ).scalar_one()
            buffered = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Ingestion)
                    .where(
                        Ingestion.tenant_id == tenant_id,
                        Ingestion.schedule_id == schedule_id,
                        Ingestion.status == "queued",
                    )
                )
            ).scalar_one()
            run_immediately = True
            if active > 0:
                if sched.overlap_policy == "skip" or buffered > 0:
                    emit_event(
                        session,
                        tenant_id=tenant_id,
                        event_type="ingestion.schedule_skipped",
                        resource_urn=f"wr:{tenant_id}:ingestion:schedule/{schedule_id}",
                        payload={
                            "schedule_id": schedule_id,
                            "reason": "previous run still active",
                            "overlap_policy": sched.overlap_policy,
                        },
                    )
                    await session.commit()
                    return {"skipped": True}
                run_immediately = False  # buffer_one: queue at most one pending run

            template = dict(sched.ingestion_template)
            if not template.get("dataset_urn"):
                # new_dataset target: mint the dataset URN on first fire and pin
                # it in the template so every later run appends to the same dataset
                template["dataset_urn"] = f"wr:{tenant_id}:dataset:dataset/{uuid7()}"
                sched.ingestion_template = template
            now = datetime.now(UTC)
            ing = Ingestion(
                id=uuid7(),
                tenant_id=tenant_id,
                workspace_id=sched.workspace_id,
                connection_id=sched.connection_id,
                dataset_urn=template.get("dataset_urn"),
                new_dataset=template.get("new_dataset"),
                ingestion_mode="query",
                file_format="parquet",  # BR-2
                statement=template.get("statement"),
                status="created",
                trigger="schedule",
                schedule_id=schedule_id,
                scheduled_for=now,
                skip_profiling=bool(template.get("skip_profiling", False)),
                allow_empty=bool(template.get("allow_empty", False)),
                error_row_limit=int(template.get("error_row_limit", 100)),
            )
            session.add(ing)
            emit_event(
                session,
                tenant_id=tenant_id,
                event_type="ingestion.created",
                resource_urn=ingestion_urn(ing),
                payload={"ingestion_id": ing.id, "schedule_id": schedule_id, "trigger": "schedule"},
            )
            emit_event(
                session,
                tenant_id=tenant_id,
                event_type="ingestion.schedule_fired",
                resource_urn=f"wr:{tenant_id}:ingestion:schedule/{schedule_id}",
                payload={"schedule_id": schedule_id, "ingestion_id": ing.id},
            )
            record_transition(session, ing, "queued", TransitionContext(payload_valid=True))
            sched.last_fired_at = now
            watermark = None
            if sched.watermark_column and sched.watermark_value is not None:
                watermark = WatermarkSpec(
                    column=sched.watermark_column,
                    operator=sched.watermark_operator,
                    value_type=sched.watermark_value_type,
                    value=sched.watermark_value,
                )
            await session.commit()
            ingestion_id = ing.id

        if not run_immediately or not self.c.settings.inline_execution:
            return {"skipped": False, "ingestion_id": ingestion_id, "buffered": not run_immediately}

        from app.domain.services.runner import IngestionRunner

        result = await IngestionRunner(self.c).execute(tenant_id, ingestion_id, watermark=watermark)
        if result.get("status") == "completed" and result.get("observed_watermark") is not None:
            # ING-FR-061: persist the high-watermark observed in this run
            async with self.c.db.tenant_session(tenant_id) as session:
                sched = (
                    await session.execute(sa.select(Schedule).where(Schedule.id == schedule_id))
                ).scalar_one()
                sched.watermark_value = result["observed_watermark"]
                await session.commit()
        return {"skipped": False, "ingestion_id": ingestion_id, **result}

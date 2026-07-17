"""Recurring pipeline scheduling (PIPE-FR-050).

``compute_next_fire`` mirrors ingestion-service's helper (croniter → the next
datetime in UTC). ``PipelineScheduleService`` owns schedule CRUD and the REAL,
poll-based fire mechanism ``fire_due(now)``: it scans DUE schedules across tenants
(via the injected scanner's worker session) and, for each, submits a real pipeline
run through the existing ``RunService.create_run`` path — no stub, exactly like
ingestion-service's InProcessScheduler tick, but DB-backed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from app.domain.entities import CallCtx, PipelineSchedule
from app.domain.errors import AppError, NotFound, ValidationFailed
from app.utils import new_id

logger = logging.getLogger(__name__)

# The scheduler acts as a service principal; scheduled runs are attributed to it
# (submitted_by) so they are distinguishable from user/agent-submitted runs.
SCHEDULER_ACTOR = {"type": "service", "id": "pipeline-scheduler"}


def compute_next_fire(cron: str, timezone: str, now: datetime | None = None) -> datetime:
    """Next fire time for a cron in the given IANA timezone, returned in UTC."""
    tz = ZoneInfo(timezone)
    base = (now or datetime.now(UTC)).astimezone(tz)
    return croniter(cron, base).get_next(datetime).astimezone(UTC)


class PipelineScheduleService:
    def __init__(self, deps, run_service, scanner):
        self.d = deps
        self.runs = run_service
        self.scanner = scanner

    # ------------------------------------------------------------ validation
    @staticmethod
    def _validate(cron: str, timezone: str) -> None:
        details = []
        if not cron or not croniter.is_valid(cron):
            details.append({"field": "cron", "message": "invalid cron expression"})
        try:
            ZoneInfo(timezone)
        except Exception:  # noqa: BLE001
            details.append({"field": "timezone", "message": "unknown IANA timezone"})
        if details:
            raise ValidationFailed("invalid schedule", code="VALIDATION_FAILED",
                                   details=details)

    # ---------------------------------------------------------------- create
    async def create(self, ctx: CallCtx, template_id: str, name: str | None,
                     cron: str, timezone: str, run_parameters: dict) -> PipelineSchedule:
        self._validate(cron, timezone)
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            now = self.d.clock.now()
            sched = PipelineSchedule(
                schedule_id=new_id(), tenant_id=ctx.tenant_id, template_id=template_id,
                name=name, cron=cron, timezone=timezone,
                run_parameters=run_parameters or {}, enabled=True,
                next_fire_at=compute_next_fire(cron, timezone, now),
                last_fire_at=None, last_run_id=None,
                created_by=ctx.actor.get("id"), created_at=now, updated_at=now)
            await uow.schedules.add(sched)
        return sched

    # ------------------------------------------------------------------ read
    async def list(self, ctx: CallCtx) -> list[PipelineSchedule]:
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            return await uow.schedules.list()

    async def get(self, ctx: CallCtx, schedule_id: str) -> PipelineSchedule:
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            sched = await uow.schedules.get(schedule_id)
            if sched is None:
                raise NotFound("schedule not found")
            return sched

    # -------------------------------------------------------- pause / resume
    async def pause(self, ctx: CallCtx, schedule_id: str) -> PipelineSchedule:
        return await self._set_enabled(ctx, schedule_id, False)

    async def resume(self, ctx: CallCtx, schedule_id: str) -> PipelineSchedule:
        return await self._set_enabled(ctx, schedule_id, True)

    async def _set_enabled(self, ctx, schedule_id, enabled) -> PipelineSchedule:
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            sched = await uow.schedules.get(schedule_id)
            if sched is None:
                raise NotFound("schedule not found")
            now = self.d.clock.now()
            sched.enabled = enabled
            sched.updated_at = now
            # Resuming recomputes the next fire from now so a long-paused schedule
            # doesn't fire a backlog burst; pausing leaves next_fire_at as-is
            # (fire_due skips disabled rows anyway).
            if enabled:
                sched.next_fire_at = compute_next_fire(sched.cron, sched.timezone, now)
            await uow.schedules.update(sched)
            return sched

    # ---------------------------------------------------------------- delete
    async def delete(self, ctx: CallCtx, schedule_id: str) -> None:
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            sched = await uow.schedules.get(schedule_id)
            if sched is None:
                raise NotFound("schedule not found")
            await uow.schedules.delete(schedule_id)

    # -------------------------------------------------------------- run now
    async def run_now(self, ctx: CallCtx, schedule_id: str):
        """Fire the schedule immediately (out of band), without advancing the cron.
        Returns (schedule, run)."""
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            sched = await uow.schedules.get(schedule_id)
            if sched is None:
                raise NotFound("schedule not found")
        run = await self._fire_one(sched, advance=False, now=self.d.clock.now())
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            sched = await uow.schedules.get(schedule_id)
        return sched, run

    # ------------------------------------------------------- fire mechanism
    async def fire_due(self, now: datetime | None = None) -> list:
        """Poll-based fire: submit a real run for every enabled schedule whose
        next_fire_at is due, then advance its next_fire_at. Returns the created
        PipelineRun objects (the caller/ticker drives submitted runs)."""
        now = now or self.d.clock.now()
        due = await self.scanner.due(now)
        fired = []
        for sched in due:
            try:
                run = await self._fire_one(sched, advance=True, now=now)
                if run is not None:
                    fired.append(run)
            except Exception as exc:  # noqa: BLE001
                # One schedule's failure (e.g. draft template, rate limit) must not
                # abort the tick or hot-loop the row: advance next_fire_at regardless.
                lvl = logger.info if isinstance(exc, AppError) else logger.exception
                lvl("scheduled run for %s failed: %s", sched.schedule_id, exc)
                await self._advance_only(sched, now)
        return fired

    async def _fire_one(self, sched: PipelineSchedule, *, advance: bool,
                        now: datetime):
        ctx = CallCtx(tenant_id=sched.tenant_id, actor=dict(SCHEDULER_ACTOR))
        _op, run = await self.runs.create_run(
            ctx, sched.template_id, dict(sched.run_parameters or {}),
            trigger="schedule")
        # Record last fire + advance the cron in the SAME tenant-scoped session so
        # RLS WITH CHECK governs the write.
        async with self.d.uow_factory(sched.tenant_id) as uow:
            fresh = await uow.schedules.get(sched.schedule_id)
            if fresh is not None:
                fresh.last_fire_at = now
                fresh.last_run_id = run.id
                fresh.updated_at = now
                if advance:
                    fresh.next_fire_at = compute_next_fire(fresh.cron, fresh.timezone, now)
                await uow.schedules.update(fresh)
        return run

    async def _advance_only(self, sched: PipelineSchedule, now: datetime) -> None:
        try:
            async with self.d.uow_factory(sched.tenant_id) as uow:
                fresh = await uow.schedules.get(sched.schedule_id)
                if fresh is not None:
                    fresh.next_fire_at = compute_next_fire(fresh.cron, fresh.timezone, now)
                    fresh.updated_at = now
                    await uow.schedules.update(fresh)
        except Exception:  # noqa: BLE001
            logger.exception("failed to advance next_fire for %s", sched.schedule_id)

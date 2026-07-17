"""Scheduled scoring (INF-FR-050..055).

A schedule is a real Postgres row; a background scheduler tick computes next-fire
from ``cron`` (IANA timezone, croniter) or ``interval_seconds`` and fires due
schedules. Each fire resolves the model (pinned or by stage_selector at fire
time) and the input selector fresh (BR-8), then submits a normal job with
``schedule_id`` set. Overlap policy, the consecutive-failure circuit breaker and
pause/resume/trigger are handled here.

In production the durable substrate is a Temporal Schedule per row; locally the
Postgres-backed tick is the real, deterministic equivalent (the resolution,
overlap and breaker logic is identical and fully tested).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter

from app.domain.entities import ScoringSchedule
from app.domain.enums import (
    NON_TERMINAL,
    JobStatus,
    ModelStage,
    OverlapPolicy,
    overlap_from_str,
    stage_name,
)
from app.domain.errors import Conflict, NotFound, RateLimited, ValidationFailed
from app.domain.ports import CallCtx, ServiceDeps
from app.domain.services import InferenceService, SubmitRequest, make_envelope
from app.domain.urn import parse, schedule_urn
from app.utils import utcnow, uuid7

EVENTS_TOPIC = "inference.events.v1"

_STAGE_BY_NAME = {"production": ModelStage.production, "staging": ModelStage.staging,
                  "none": ModelStage.none, "archived": ModelStage.archived}


class ScheduleService:
    def __init__(self, deps: ServiceDeps, jobs: InferenceService):
        self.deps = deps
        self.jobs = jobs

    def _next_fire(self, sch: ScoringSchedule, after: datetime) -> datetime:
        if sch.cron:
            tz = ZoneInfo(sch.timezone or "UTC")
            base = after.astimezone(tz)
            nxt = croniter(sch.cron, base).get_next(datetime)
            return nxt.astimezone(UTC)
        return after + timedelta(seconds=sch.interval_seconds or 3600)

    async def create(self, ctx: CallCtx, body: dict) -> ScoringSchedule:
        name = body.get("name")
        if not name:
            raise ValidationFailed("schedule name required")
        pinned = body.get("model_version_urn")
        model_urn = body.get("model_urn")
        stage_sel = body.get("stage_selector")
        if bool(pinned) == bool(model_urn):
            raise ValidationFailed("exactly one of model_version_urn / model_urn required")
        if model_urn and not stage_sel:
            raise ValidationFailed("model_urn requires stage_selector")
        cron = body.get("cron")
        interval = body.get("interval_seconds")
        if bool(cron) == bool(interval):
            raise ValidationFailed("exactly one of cron / interval_seconds required")
        if cron and not croniter.is_valid(cron):
            raise ValidationFailed(f"invalid cron {cron!r}")
        output = body.get("output") or {}
        if "mode" not in output:
            output["mode"] = "append"
        now = self.deps.clock.now()
        sid = str(uuid7())
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            if await uow.schedules.get_by_name(ctx.workspace_id, name):
                raise Conflict(f"schedule {name!r} already exists")
            if await uow.schedules.count_enabled() >= self.deps.settings.max_enabled_schedules:
                raise RateLimited("enabled schedule quota exhausted")
            sch = ScoringSchedule(
                id=sid, tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id, name=name,
                input_selector=body["input_selector"], output=output,
                created_by=ctx.actor.get("id", ""), created_at=now, updated_at=now,
                model_version_urn=pinned, model_urn=model_urn,
                stage_selector=int(_STAGE_BY_NAME[stage_sel]) if stage_sel else None,
                cron=cron, interval_seconds=interval, timezone=body.get("timezone", "UTC"),
                overlap_policy=int(overlap_from_str(body.get("overlap_policy"))),
                enabled=body.get("enabled", True),
                temporal_schedule_id=schedule_urn(ctx.tenant_id, sid),
                notify_on_failure=body.get("notify_on_failure", True),
            )
            sch.next_fire_at = self._next_fire(sch, now) if sch.enabled else None
            await uow.schedules.add(sch)
            await self._emit(uow, ctx, "inference.schedule.created", sch,
                             {"schedule_id": sch.id, "name": sch.name, "enabled": sch.enabled})
        return sch

    async def get(self, ctx: CallCtx, sid: str) -> ScoringSchedule:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sch = await uow.schedules.get(sid)
            if sch is None:
                raise NotFound("schedule not found")
            return sch

    async def list(self, ctx: CallCtx, limit: int, cursor: str | None):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.schedules.list(limit, cursor)

    async def update(self, ctx: CallCtx, sid: str, patch: dict) -> ScoringSchedule:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sch = await uow.schedules.get(sid)
            if sch is None:
                raise NotFound("schedule not found")
            for field in ("cron", "interval_seconds", "timezone", "notify_on_failure",
                          "input_selector", "output"):
                if field in patch and patch[field] is not None:
                    setattr(sch, field, patch[field])
            if "overlap_policy" in patch and patch["overlap_policy"]:
                sch.overlap_policy = int(overlap_from_str(patch["overlap_policy"]))
            if sch.cron and not croniter.is_valid(sch.cron):
                raise ValidationFailed(f"invalid cron {sch.cron!r}")
            sch.updated_at = self.deps.clock.now()
            if sch.enabled:
                sch.next_fire_at = self._next_fire(sch, sch.updated_at)
            await uow.schedules.update(sch)
            await self._emit(uow, ctx, "inference.schedule.updated", sch,
                             {"schedule_id": sch.id, "name": sch.name, "enabled": sch.enabled})
            return sch

    async def pause(self, ctx: CallCtx, sid: str, reason: str = "USER_PAUSED") -> ScoringSchedule:
        return await self._set_enabled(ctx, sid, False, reason, "inference.schedule.paused")

    async def resume(self, ctx: CallCtx, sid: str) -> ScoringSchedule:
        return await self._set_enabled(ctx, sid, True, None, "inference.schedule.resumed")

    async def _set_enabled(self, ctx, sid, enabled, reason, event) -> ScoringSchedule:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sch = await uow.schedules.get(sid)
            if sch is None:
                raise NotFound("schedule not found")
            sch.enabled = enabled
            sch.paused_reason = reason
            now = self.deps.clock.now()
            sch.updated_at = now
            if enabled:
                sch.consecutive_failures = 0  # resume resets the breaker (BR-9)
                sch.next_fire_at = self._next_fire(sch, now)
            else:
                sch.next_fire_at = None
            await uow.schedules.update(sch)
            await self._emit(uow, ctx, event, sch,
                             {"schedule_id": sch.id, "name": sch.name, "enabled": enabled,
                              "paused_reason": reason})
            return sch

    async def delete(self, ctx: CallCtx, sid: str) -> None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sch = await uow.schedules.get(sid)
            if sch is None:
                raise NotFound("schedule not found")
            sch.deleted_at = self.deps.clock.now()
            sch.enabled = False
            sch.next_fire_at = None
            await uow.schedules.update(sch)
            await self._emit(uow, ctx, "inference.schedule.deleted", sch,
                             {"schedule_id": sch.id, "name": sch.name, "enabled": False})

    async def trigger(self, ctx: CallCtx, sid: str) -> dict:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sch = await uow.schedules.get(sid, include_deleted=False)
            if sch is None:
                raise NotFound("schedule not found")
        return await self.fire(sch, forced=True)

    async def fire(self, sch: ScoringSchedule, *, forced: bool = False) -> dict:
        """Resolve model + input fresh and submit one job. Returns a fire result."""
        ctx = CallCtx(tenant_id=sch.tenant_id,
                      actor={"type": "service", "id": "inference-scheduler"},
                      workspace_id=sch.workspace_id, submitted_by=sch.created_by)
        # overlap policy (BR-7)
        if not forced:
            overlap = await self._overlap_block(ctx, sch)
            if overlap is not None:
                return overlap
        # resolve model (pinned or by stage at fire time, BR-8/AC-9)
        try:
            model_version_urn = await self._resolve_model_urn(ctx, sch)
        except _NoModelInStage:
            await self._fire_skipped(ctx, sch, "NO_MODEL_IN_STAGE")
            return {"fired": False, "reason": "NO_MODEL_IN_STAGE"}
        # resolve input selector fresh
        try:
            input_urn = self._resolve_input_urn(sch)
        except Exception:  # noqa: BLE001
            await self._fire_skipped(ctx, sch, "INPUT_RESOLUTION_FAILED")
            await self._record_failure(ctx, sch)
            return {"fired": False, "reason": "INPUT_RESOLUTION_FAILED"}

        req = SubmitRequest(
            model_version_urn=model_version_urn, input_dataset_urn=input_urn,
            output=dict(sch.output), schedule_id=sch.id, allow_empty=True)
        try:
            job = await self.jobs.submit(ctx, req)
        except Exception as exc:  # noqa: BLE001
            await self._record_failure(ctx, sch)
            return {"fired": False, "reason": "SUBMIT_FAILED", "error": str(exc)}
        # count a rejected job as a failed fire toward the breaker
        if job.status == int(JobStatus.rejected):
            await self._record_failure(ctx, sch)
        else:
            await self._record_success(ctx, sch)
        return {"fired": True, "job_id": job.id, "status": int(job.status)}

    async def _overlap_block(self, ctx, sch) -> dict | None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            last = await uow.jobs.last_for_schedule(sch.id)
        if last is None or JobStatus(last.status) not in NON_TERMINAL:
            return None
        policy = OverlapPolicy(sch.overlap_policy)
        if policy == OverlapPolicy.skip:
            await self._fire_skipped(ctx, sch, "OVERLAP")
            return {"fired": False, "reason": "OVERLAP"}
        if policy == OverlapPolicy.queue:
            # BR-7: queue allows at most ONE pending (queued) fire; if one is
            # already pending, further fires skip. Otherwise let this fire proceed
            # (it becomes the single pending job when capacity is full).
            async with self.deps.uow_factory(ctx.tenant_id) as uow:
                from app.domain.ports import Filters

                pending = await uow.jobs.list(
                    Filters(schedule_id=sch.id, status=int(JobStatus.queued)),
                    "-created_at", 1, None)
            if pending.items:
                await self._fire_skipped(ctx, sch, "OVERLAP")
                return {"fired": False, "reason": "OVERLAP"}
            return None
        if policy == OverlapPolicy.cancel_running:
            try:
                await self.jobs.cancel(ctx, last.id)
            except Exception:  # noqa: BLE001
                pass
        return None

    async def _resolve_model_urn(self, ctx, sch) -> str:
        if sch.model_version_urn:
            return sch.model_version_urn
        parsed = parse(sch.model_urn)
        stage = stage_name(sch.stage_selector)
        resolved = await self.deps.registry.resolve_by_stage(parsed.resource_id, stage)
        if resolved is None:
            raise _NoModelInStage()
        from app.domain.urn import model_version_urn

        return model_version_urn(sch.tenant_id, resolved.model_id, resolved.version)

    def _resolve_input_urn(self, sch) -> str:
        sel = sch.input_selector
        urn = sel.get("dataset_urn")
        if not urn:
            raise ValueError("input_selector.dataset_urn required")
        return urn

    async def _fire_skipped(self, ctx, sch, reason: str) -> None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            await self._emit(uow, ctx, "inference.schedule.fire_skipped", sch,
                             {"schedule_id": sch.id, "fire_at": utcnow().isoformat(),
                              "reason": reason})

    async def _record_success(self, ctx, sch) -> None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            fresh = await uow.schedules.get(sch.id)
            if fresh is None:
                return
            fresh.consecutive_failures = 0
            fresh.last_fired_at = self.deps.clock.now()
            fresh.next_fire_at = self._next_fire(fresh, fresh.last_fired_at)
            await uow.schedules.update(fresh)

    async def _record_failure(self, ctx, sch) -> None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            fresh = await uow.schedules.get(sch.id)
            if fresh is None:
                return
            fresh.consecutive_failures += 1
            fresh.last_fired_at = self.deps.clock.now()
            fresh.next_fire_at = self._next_fire(fresh, fresh.last_fired_at)
            if fresh.consecutive_failures >= self.deps.settings.schedule_circuit_breaker:
                fresh.enabled = False
                fresh.paused_reason = "AUTO_PAUSED_CONSECUTIVE_FAILURES"
                fresh.next_fire_at = None
                await uow.schedules.update(fresh)
                await self._emit(uow, ctx, "inference.schedule.auto_paused", fresh,
                                 {"schedule_id": fresh.id,
                                  "consecutive_failures": fresh.consecutive_failures})
                if self.deps.notifier is not None:
                    await self.deps.notifier.notify(
                        tenant_id=ctx.tenant_id, recipient=fresh.created_by,
                        kind="schedule_auto_paused", detail={"schedule_id": fresh.id})
                return
            await uow.schedules.update(fresh)

    async def _emit(self, uow, ctx, event_type, sch, payload) -> None:
        env = make_envelope(event_type=event_type, ctx=ctx,
                            resource_urn=schedule_urn(sch.tenant_id, sch.id), payload=payload)
        await uow.outbox.add(EVENTS_TOPIC, env)


class _NoModelInStage(Exception):
    pass

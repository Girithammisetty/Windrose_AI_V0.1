"""Scheduler port (ING-FR-060/062/063).

InProcessScheduler is an APScheduler-style in-process implementation used in
dev/tests: it computes next-fire times from cron/interval specs and fires a
bound async callback (tests drive `tick`/`run_now` deterministically; a
background loop is available via start()). TemporalScheduler is the production
adapter stub (TODO: Temporal Schedules).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from croniter import croniter

FireCallback = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class ScheduleEntry:
    schedule_id: str
    cron: str | None
    interval_seconds: int | None
    timezone: str
    paused: bool
    next_fire_at: datetime | None


class Scheduler(Protocol):
    def bind(self, callback: FireCallback) -> None: ...

    async def register(
        self,
        schedule_id: str,
        *,
        cron: str | None,
        interval_seconds: int | None,
        timezone: str,
    ) -> str: ...

    async def unregister(self, schedule_id: str) -> None: ...

    async def pause(self, schedule_id: str) -> None: ...

    async def resume(self, schedule_id: str) -> None: ...

    async def run_now(self, schedule_id: str) -> None: ...

    def next_fire_at(self, schedule_id: str) -> datetime | None: ...


def compute_next_fire(
    cron: str | None, interval_seconds: int | None, timezone: str, now: datetime | None = None
) -> datetime:
    tz = ZoneInfo(timezone)
    base = (now or datetime.now(UTC)).astimezone(tz)
    if cron:
        return croniter(cron, base).get_next(datetime).astimezone(UTC)
    assert interval_seconds is not None
    return (base + timedelta(seconds=interval_seconds)).astimezone(UTC)


class InProcessScheduler:
    def __init__(self) -> None:
        self._entries: dict[str, ScheduleEntry] = {}
        self._callback: FireCallback | None = None
        self._task: asyncio.Task[None] | None = None

    def bind(self, callback: FireCallback) -> None:
        self._callback = callback

    async def register(
        self,
        schedule_id: str,
        *,
        cron: str | None,
        interval_seconds: int | None,
        timezone: str,
    ) -> str:
        self._entries[schedule_id] = ScheduleEntry(
            schedule_id=schedule_id,
            cron=cron,
            interval_seconds=interval_seconds,
            timezone=timezone,
            paused=False,
            next_fire_at=compute_next_fire(cron, interval_seconds, timezone),
        )
        return f"inproc-{schedule_id}"

    async def unregister(self, schedule_id: str) -> None:
        self._entries.pop(schedule_id, None)

    async def pause(self, schedule_id: str) -> None:
        if schedule_id in self._entries:
            self._entries[schedule_id].paused = True

    async def resume(self, schedule_id: str) -> None:
        entry = self._entries.get(schedule_id)
        if entry:
            entry.paused = False
            entry.next_fire_at = compute_next_fire(
                entry.cron, entry.interval_seconds, entry.timezone
            )

    async def run_now(self, schedule_id: str) -> None:
        if self._callback is None:
            raise RuntimeError("scheduler callback not bound")
        await self._callback(schedule_id)

    def next_fire_at(self, schedule_id: str) -> datetime | None:
        entry = self._entries.get(schedule_id)
        return entry.next_fire_at if entry else None

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Fire all due entries; returns fired schedule ids (test-drivable)."""
        now = now or datetime.now(UTC)
        fired = []
        for entry in list(self._entries.values()):
            if entry.paused or entry.next_fire_at is None or entry.next_fire_at > now:
                continue
            entry.next_fire_at = compute_next_fire(
                entry.cron, entry.interval_seconds, entry.timezone, now
            )
            if self._callback is not None:
                await self._callback(entry.schedule_id)
            fired.append(entry.schedule_id)
        return fired

    def start(self, poll_interval: float = 1.0) -> None:
        async def _loop() -> None:
            while True:
                await asyncio.sleep(poll_interval)
                await self.tick()

        self._task = asyncio.get_running_loop().create_task(_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


class TemporalScheduler:
    """Production adapter stub — NOT wired anywhere (both container modes use
    InProcessScheduler). Every method raises an HONEST 501 NOT_IMPLEMENTED so
    that if it is ever wired by mistake, schedule create/update/pause/resume
    APIs reject at request time with a clear error instead of surfacing a 500
    or, worse, persisting schedules that silently never fire.

    TODO(wave-2): temporalio ScheduleClient — one Temporal Schedule per row
    (id `ing-sched-<schedule_id>`), overlap policy mapped to
    ScheduleOverlapPolicy.SKIP / BUFFER_ONE (ING-FR-060, BR-10).
    """

    def __init__(self, target: str, namespace: str = "windrose") -> None:
        self.target = target
        self.namespace = namespace

    @staticmethod
    def _not_implemented(op: str):
        from app.domain.errors import NotImplementedFeatureError

        return NotImplementedFeatureError(
            f"TemporalScheduler.{op} is not implemented in this deployment "
            "(TODO wave-2 temporalio); schedules cannot be accepted against it"
        )

    def bind(self, callback: FireCallback) -> None:  # noqa: ARG002 - stub
        raise self._not_implemented("bind")

    async def register(
        self, schedule_id: str, *, cron: str | None, interval_seconds: int | None, timezone: str
    ) -> str:
        raise self._not_implemented("register")

    async def unregister(self, schedule_id: str) -> None:
        raise self._not_implemented("unregister")

    async def pause(self, schedule_id: str) -> None:
        raise self._not_implemented("pause")

    async def resume(self, schedule_id: str) -> None:
        raise self._not_implemented("resume")

    async def run_now(self, schedule_id: str) -> None:
        raise self._not_implemented("run_now")

    def next_fire_at(self, schedule_id: str) -> datetime | None:
        raise self._not_implemented("next_fire_at")

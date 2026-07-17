"""Real background workers for the runtime (started from app.main lifespan).

* Outbox relay — publishes committed ``outbox`` rows to real Kafka (MASTER-FR-034).
* Kafka consumers — pipeline/experiment/dataset/usage topics driving the handlers,
  each with Redis dedup + DLQ (via the shared ``windrose_common`` KafkaConsumer).
* Scheduler tick — fires due scoring schedules (INF-FR-050).
* Reaper — fails jobs stuck past max duration / queued timeout (INF-FR-042).

All loops are defensive: a transient error is logged and retried, never crashing
the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)


class WorkerSet:
    def __init__(self, container, session_factory):
        self.c = container
        self.settings = container.settings
        self.session_factory = session_factory
        self._tasks: list[asyncio.Task] = []
        self._producer = None
        self._consumers: list = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        s = self.settings
        from windrose_common.kafka import KafkaConfig, KafkaProducerClient

        self._producer = KafkaProducerClient(
            KafkaConfig(bootstrap_servers=s.kafka_bootstrap_servers))
        await self._producer.start()

        if s.outbox_relay_enabled:
            self._tasks.append(asyncio.create_task(self._outbox_loop()))
        if s.scheduler_enabled:
            self._tasks.append(asyncio.create_task(self._scheduler_loop()))
            self._tasks.append(asyncio.create_task(self._reaper_loop()))
        if s.consumers_enabled:
            await self._start_consumers()

    async def _start_consumers(self) -> None:
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        from app.events.consumer import (
            DatasetEventHandler,
            ExperimentEventHandler,
            PipelineEventHandler,
            UsageEventHandler,
        )

        s = self.settings
        cfg = KafkaConfig(bootstrap_servers=s.kafka_bootstrap_servers)
        specs = [
            (s.pipeline_events_topic, "inference-pipeline",
             PipelineEventHandler(self.c.inference).handle),
            (s.experiment_events_topic, "inference-experiment",
             ExperimentEventHandler(self.c.inference).handle),
            (s.dataset_events_topic, "inference-dataset",
             DatasetEventHandler(self.c.inference).handle),
            (s.usage_events_topic, "inference-usage",
             UsageEventHandler(self.c.budget_gate).handle),
        ]
        for topic, group, handler in specs:
            consumer = KafkaConsumer(topic, group, handler, self.c.dedup, self._producer,
                                     cfg=cfg)
            try:
                await consumer.start()
            except Exception:  # noqa: BLE001
                logger.exception("consumer start failed for %s", topic)
                continue
            self._consumers.append(consumer)
            self._tasks.append(asyncio.create_task(self._run_consumer(consumer)))

    async def _run_consumer(self, consumer) -> None:
        while not self._stop.is_set():
            try:
                await consumer.consume_batch(max_messages=50, timeout_ms=1000)
            except Exception:  # noqa: BLE001
                logger.exception("consumer loop error (%s)", consumer.topic)
                await asyncio.sleep(1.0)

    async def _outbox_loop(self) -> None:
        from app.events.bus import KafkaEventBus
        from app.store.sql import OutboxDispatcher

        bus = KafkaEventBus(self.settings.kafka_bootstrap_servers)
        dispatcher = OutboxDispatcher(self.session_factory, bus)
        while not self._stop.is_set():
            try:
                n = await dispatcher.run_once()
                await asyncio.sleep(0.2 if n else 0.5)
            except Exception:  # noqa: BLE001
                logger.exception("outbox relay error")
                await asyncio.sleep(1.0)
        await bus.aclose()

    async def _scheduler_loop(self) -> None:
        from datetime import UTC, datetime

        while not self._stop.is_set():
            try:
                async with self.c.deps.uow_factory("*", worker=True) as uow:
                    due = [
                        s for s in await uow.schedules.all_enabled()
                        if s.next_fire_at is not None and s.next_fire_at <= datetime.now(UTC)
                    ]
                for sch in due:
                    with contextlib.suppress(Exception):
                        await self.c.schedules.fire(sch)
            except Exception:  # noqa: BLE001
                logger.exception("scheduler tick error")
            await asyncio.sleep(self.settings.scheduler_tick_seconds)

    async def _reaper_loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                await self.c.inference.reap("*")
            await asyncio.sleep(60.0)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for consumer in self._consumers:
            with contextlib.suppress(Exception):
                await consumer.stop()
        if self._producer is not None:
            with contextlib.suppress(Exception):
                await self._producer.stop()

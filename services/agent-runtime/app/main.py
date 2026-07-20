"""FastAPI application factory (real adapters by default; CONVENTIONS END STATE).

Lifespan wires the running service to real infra: seeds the agent catalog into
Postgres, connects the Temporal client + starts an in-process worker (so
``app.main:app`` both serves the API and executes AgentRunWorkflow durably),
starts the transactional-outbox relay to Kafka (MASTER-FR-034), and disposes
engines on shutdown.

Degradations are LOUD, never silent: a failed store seed / Temporal connect /
relay start is logged with the traceback and recorded in the container extras
so /readyz reports the actual execution mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from windrose_common.logging import configure_json_logging

from app.agents.catalog import seed_catalog
from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.routes import (
    a2a,
    chat,
    decisions,
    entity_merges,
    health,
    jwks,
    outcomes,
    proposals,
    registry,
    replay,
    sft,
    training,
    transcripts,
)
from app.container import Container, build_container

configure_json_logging("agent-runtime")  # MASTER-FR-050: JSON stdout, mirrors Go's slog handler
logger = logging.getLogger("agent-runtime")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    c: Container = app.state.container
    tasks: list[asyncio.Task] = []

    # Seed the agent catalog (idempotent). Non-fatal, but NEVER silent — a
    # failed seed means no published agents and every chat 404s.
    try:
        await c.store.connect()
        await seed_catalog(c.store, c.signing_key)
        c.extras["catalog_seeded"] = True
    except Exception:
        c.extras["catalog_seeded"] = False
        logger.exception(
            "agent catalog seed FAILED — chat will 404 until the store is reachable")

    if c.settings.use_temporal and c.settings.use_real_adapters:
        # A failed Temporal connect silently downgraded runs to the inline
        # engine before; keep the run path alive but SAY SO and expose it.
        try:
            from app.runtime.temporalx.worker import build_worker, connect

            client = await connect(c.settings.temporal_target, c.settings.temporal_namespace)
            c.extras["temporal_client"] = client
            worker = build_worker(client, c, task_queue=c.settings.temporal_task_queue)
            tasks.append(asyncio.create_task(worker.run()))
            logger.info("temporal worker started on %s", c.settings.temporal_task_queue)
        except Exception:
            logger.exception(
                "Temporal connect FAILED — runs will execute INLINE (no durable "
                "workflow) until Temporal at %s is reachable", c.settings.temporal_target)

    # Transactional-outbox relay (MASTER-FR-034): drain unpublished outbox rows
    # to Kafka. Same pattern as pipeline-orchestrator's _start_workers relay.
    if c.settings.use_real_adapters and c.extras.get("session_factory") is not None:
        try:
            from app.store.sql import OutboxDispatcher

            dispatcher = OutboxDispatcher(c.extras["session_factory"], c.bus)

            async def relay_loop():
                while True:
                    try:
                        n = await dispatcher.run_once()
                    except Exception:  # noqa: BLE001
                        logger.exception("outbox relay error")
                        n = 0
                    await asyncio.sleep(0.2 if n else 1.0)

            tasks.append(asyncio.create_task(relay_loop()))
            c.extras["outbox_relay"] = True
            logger.info("outbox relay started")
        except Exception:
            c.extras["outbox_relay"] = False
            logger.exception("outbox relay FAILED to start — events will pile up unpublished")

    # Scheduled drift-driven retrain loop (BRD 52 inc3): tick due retrain-watches,
    # compute the correction-rate drift signal, and open governance retrain
    # proposals over threshold. Only proposes — a human approves every retrain.
    if c.settings.use_real_adapters:
        try:
            from app.runtime.retrain_scheduler import RetrainScheduler

            interval = float(os.getenv("AR_RETRAIN_SCHEDULER_INTERVAL_S", "300"))
            scheduler = RetrainScheduler(c, interval_seconds=interval)
            tasks.append(asyncio.create_task(scheduler.run()))
            c.extras["retrain_scheduler"] = True
            logger.info("retrain scheduler started (interval=%ss)", interval)
        except Exception:
            c.extras["retrain_scheduler"] = False
            logger.exception("retrain scheduler FAILED to start")

    # Event-driven decisioning: a domain event fires a GOVERNED autonomous agent
    # run in seconds instead of waiting for a human or the next schedule tick.
    # Every governance gate still applies downstream (toolset allow-list, risk
    # tiering, Rule-of-Two, tenant auto-execute policy) — see event_triggers.py.
    # Off unless explicitly enabled: turning it on starts auto-triggering agents.
    if c.settings.use_real_adapters and c.settings.event_triggers_enabled:
        try:
            import redis.asyncio as aioredis
            from windrose_common.kafka import KafkaConfig, KafkaConsumer, KafkaProducerClient
            from windrose_common.redisx import RedisDedupStore

            from app.runtime.event_triggers import EventTriggerDispatcher

            kcfg = KafkaConfig(bootstrap_servers=c.settings.kafka_bootstrap_servers)
            producer = KafkaProducerClient(kcfg)  # DLQ sink for poison messages
            await producer.start()
            c.extras["event_trigger_producer"] = producer
            # Redis dedup is handle-then-mark, so a redelivered event can never
            # fire a duplicate agent run once the first one's effects are durable.
            dedup = RedisDedupStore(aioredis.from_url(c.settings.redis_url))
            topic = c.settings.event_trigger_topic
            consumer = KafkaConsumer(
                topic, "agent-runtime.event-triggers",
                EventTriggerDispatcher(c).handle, dedup, producer, cfg=kcfg)
            await consumer.start()
            tasks.append(asyncio.create_task(consumer.run()))
            c.extras["event_triggers"] = True
            logger.info("event triggers started (topic=%s)", topic)
        except Exception:
            c.extras["event_triggers"] = False
            logger.exception("event triggers FAILED to start — no real-time decisioning")

    yield

    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(Exception):
            await t
    if c.extras.get("event_trigger_producer") is not None:
        with contextlib.suppress(Exception):
            await c.extras["event_trigger_producer"].stop()
    for engine in c.extras.get("engines", []):
        with contextlib.suppress(Exception):
            await engine.dispose()


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    app = FastAPI(title="agent-runtime", version="0.1.0", docs_url="/docs",
                  lifespan=_lifespan)
    app.state.container = container
    from windrose_common.metricsx import RedMiddleware, instrument_app
    from windrose_common.otelx import configure_tracing
    configure_tracing("agent-runtime")
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="agent-runtime")
    instrument_app(app, "agent-runtime")
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(jwks.router)
    app.include_router(chat.router)
    app.include_router(replay.router)
    app.include_router(proposals.router)
    app.include_router(registry.router)
    app.include_router(a2a.router)
    app.include_router(transcripts.router)
    app.include_router(sft.router)
    app.include_router(training.router)
    app.include_router(decisions.router)
    app.include_router(outcomes.router)
    app.include_router(entity_merges.router)
    return app


app = create_app()

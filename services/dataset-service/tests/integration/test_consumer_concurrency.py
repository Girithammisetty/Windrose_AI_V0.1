"""Integration: event-consumer idempotency against durable dedup (AC-1) and
concurrent version registration under the advisory lock (AC-9, BR-2)."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest
from sqlalchemy import text

from app.domain.services import CallCtx
from tests.conftest import TENANT_A, ingestion_envelope

pytestmark = pytest.mark.integration

DF = pd.DataFrame({"order_id": [1, 2, 3]})


async def _count(engine, table: str, tenant=TENANT_A) -> int:
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant}
        )
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar()


class TestConsumerIdempotency:
    async def test_ac1_duplicate_event_creates_nothing_twice(self, app, container, engine):
        """AC-1: dataset(processing) + v1 + ingested edge + pending-then-terminal
        profile exist once; duplicate delivery is a no-op (processed_events dedup)."""
        await container.catalog.commit_snapshot("bronze.t.orders", 1001, DF)
        env = ingestion_envelope(TENANT_A, "ing-int-1")

        await container.bus.publish("ingestion.events.v1", env)
        assert await _count(engine, "datasets") == 1
        assert await _count(engine, "dataset_versions") == 1
        assert await _count(engine, "lineage_edges") == 1
        assert await _count(engine, "profiles") == 1

        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A}
            )
            row = (await conn.execute(text(
                "SELECT d.status, v.version_no, v.iceberg_snapshot_id, v.profile_status "
                "FROM datasets d JOIN dataset_versions v ON v.dataset_id = d.id"
            ))).one()
        # in-process profiler already completed synchronously
        assert row[0] == "ready"
        assert row[1] == 1
        assert row[2] == 1001
        assert row[3] == "completed"

        # exact duplicate (same event_id)
        await container.bus.publish("ingestion.events.v1", env)
        # broker re-key (new event_id, same ingestion_id)
        await container.bus.publish(
            "ingestion.events.v1", ingestion_envelope(TENANT_A, "ing-int-1")
        )
        assert await _count(engine, "datasets") == 1
        assert await _count(engine, "dataset_versions") == 1
        assert await _count(engine, "lineage_edges") == 1
        assert await _count(engine, "profiles") == 1

    async def test_dedup_rows_recorded(self, app, container, engine):
        await container.catalog.commit_snapshot("bronze.t.orders", 1002, DF)
        env = ingestion_envelope(TENANT_A, "ing-int-2", snapshot_id=1002,
                                 dataset_name="Dedup")
        await container.bus.publish("ingestion.events.v1", env)
        await container.bus.publish("ingestion.events.v1", env)
        assert await _count(engine, "processed_events") == 1


class TestConcurrentRegistration:
    async def test_ac9_concurrent_registrations_get_consecutive_numbers(
        self, client, container
    ):
        """AC-9: two concurrent registrations -> consecutive version_no, no gap or
        duplicate (pg_advisory_xact_lock serializes)."""
        from tests.conftest import create_dataset

        ds = await create_dataset(client, name="Contended")
        for snap in (1, 2, 3, 4):
            await container.catalog.commit_snapshot(ds["iceberg_table"], snap, DF)

        ctx = CallCtx(tenant_id=TENANT_A, actor={"type": "service", "id": "ingestion"})

        async def register(snapshot_id: int):
            return await container.version_service.register(
                ctx, ds["id"],
                {"iceberg_snapshot_id": snapshot_id, "schema": {},
                 "skip_profiling": True},
            )

        results = await asyncio.gather(register(1), register(2), register(3), register(4))
        numbers = sorted(v.version_no for v in results)
        assert numbers == [1, 2, 3, 4]  # consecutive, no gaps, no duplicates

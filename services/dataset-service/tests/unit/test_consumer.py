"""Unit: ingestion.events.v1 consumer — AC-1 (creation + dedup idempotency),
auto lineage (DST-FR-044), ingestion.failed handling (BRD §6)."""

from __future__ import annotations

import pandas as pd

from tests.conftest import TENANT_A, ingestion_envelope

DF = pd.DataFrame({"order_id": [1, 2, 3]})


async def seed_snapshot(container, table="bronze.t.orders", snapshot_id=1001):
    await container.catalog.commit_snapshot(table, snapshot_id, DF)


class TestIngestionCompleted:
    async def test_ac1_creates_dataset_version_edge_profile(self, recording_container):
        """AC-1: dataset processing, v1 with the event's snapshot, ingested edge,
        pending profile — and duplicate delivery creates none of them twice."""
        c = recording_container
        await seed_snapshot(c)
        env = ingestion_envelope(
            TENANT_A, "ing-1",
            connection_urn=f"wr:{TENANT_A}:ingestion:connection/conn-1",
        )
        await c.bus.publish("ingestion.events.v1", env)

        state = c.memory_state
        assert len(state.datasets) == 1
        dataset = next(iter(state.datasets.values()))
        assert dataset.name == "Orders"
        assert dataset.status == "processing"

        assert len(state.versions) == 1
        version = next(iter(state.versions.values()))
        assert version.version_no == 1
        assert version.iceberg_snapshot_id == 1001
        assert version.produced_by_urn == f"wr:{TENANT_A}:ingestion:ingestion/ing-1"
        assert version.profile_status == "pending"

        assert len(state.profiles) == 1
        assert next(iter(state.profiles.values())).status == "pending"

        edges = list(state.edges.values())
        assert len(edges) == 1
        assert edges[0].activity == "ingested"
        assert edges[0].from_urn == f"wr:{TENANT_A}:ingestion:connection/conn-1"
        assert edges[0].to_urn.endswith("@v1")

        assert len(c.runner.specs) == 1  # profiler launched

        # ---- duplicate delivery (same event_id): nothing doubles (MASTER-FR-032)
        await c.bus.publish("ingestion.events.v1", env)
        assert len(state.datasets) == 1
        assert len(state.versions) == 1
        assert len(state.profiles) == 1
        assert len(state.edges) == 1
        assert len(c.runner.specs) == 1

    async def test_redelivery_with_new_event_id_still_idempotent(self, recording_container):
        """Natural idempotency on ingestion_id when the broker re-keys the event."""
        c = recording_container
        await seed_snapshot(c)
        env1 = ingestion_envelope(TENANT_A, "ing-7")
        env2 = ingestion_envelope(TENANT_A, "ing-7")  # fresh event_id, same ingestion
        await c.bus.publish("ingestion.events.v1", env1)
        await c.bus.publish("ingestion.events.v1", env2)
        assert len(c.memory_state.versions) == 1

    async def test_second_ingestion_appends_version(self, recording_container):
        c = recording_container
        await seed_snapshot(c, snapshot_id=1001)
        await seed_snapshot(c, snapshot_id=1002)
        await c.bus.publish("ingestion.events.v1",
                            ingestion_envelope(TENANT_A, "ing-a", snapshot_id=1001))
        await c.bus.publish("ingestion.events.v1",
                            ingestion_envelope(TENANT_A, "ing-b", snapshot_id=1002))
        state = c.memory_state
        assert len(state.datasets) == 1
        assert sorted(v.version_no for v in state.versions.values()) == [1, 2]
        dataset = next(iter(state.datasets.values()))
        current = state.versions[dataset.current_version_id]
        assert current.version_no == 2

    async def test_skip_profiling_marks_ready(self, recording_container):
        c = recording_container
        await seed_snapshot(c)
        await c.bus.publish(
            "ingestion.events.v1",
            ingestion_envelope(TENANT_A, "ing-s", skip_profiling=True),
        )
        state = c.memory_state
        assert next(iter(state.datasets.values())).status == "ready"
        assert len(state.profiles) == 0
        assert len(c.runner.specs) == 0

    async def test_auto_registers_from_real_ingestion_payload_fields(self, recording_container):
        """GAP-3 contract: the fields ingestion-service now emits on
        ingestion.completed (workspace_id, dataset_name, iceberg_table,
        iceberg_snapshot_id) drive the auto-register — the dataset the consumer
        creates carries the event's name + bronze table."""
        c = recording_container
        table = f"bronze.{TENANT_A}.ds_ing-real"
        await seed_snapshot(c, table=table, snapshot_id=2202)
        env = ingestion_envelope(
            TENANT_A, "ing-real",
            dataset_name="auto-claims-42",
            iceberg_table=table,
            snapshot_id=2202,
            skip_profiling=True,
        )
        await c.bus.publish("ingestion.events.v1", env)
        state = c.memory_state
        assert len(state.datasets) == 1
        dataset = next(iter(state.datasets.values()))
        assert dataset.name == "auto-claims-42"
        assert dataset.iceberg_table == table
        version = next(iter(state.versions.values()))
        assert version.iceberg_snapshot_id == 2202
        assert version.produced_by_urn == f"wr:{TENANT_A}:ingestion:ingestion/ing-real"

    async def test_upload_source_urn_when_no_connection(self, recording_container):
        c = recording_container
        await seed_snapshot(c)
        await c.bus.publish("ingestion.events.v1", ingestion_envelope(TENANT_A, "ing-u"))
        edges = list(c.memory_state.edges.values())
        assert edges[0].from_urn == f"wr:{TENANT_A}:ingestion:upload/ing-u"


class TestUrnSingleSourceOfTruth:
    """URN-drift fix: ingestion-service pre-mints the dataset id (it is inside
    the event's dataset_urn / dataset_id); the consumer MUST register the
    dataset row under that exact id so every consumer holding the ingestion's
    URN (case rows, lineage, the UI's Case.sourceDataset) resolves to it."""

    MINTED = "019f51cf-c296-4bbb-8bbb-0123456789ab"

    def _env(self, ingestion_id, **kw):
        return ingestion_envelope(
            TENANT_A, ingestion_id,
            dataset_urn=f"wr:{TENANT_A}:dataset:dataset/{self.MINTED}",
            dataset_id=self.MINTED,
            **kw,
        )

    async def test_dataset_created_under_event_minted_id(self, recording_container):
        c = recording_container
        await seed_snapshot(c)
        await c.bus.publish("ingestion.events.v1", self._env("ing-mint"))
        state = c.memory_state
        assert list(state.datasets.keys()) == [self.MINTED]
        dataset = state.datasets[self.MINTED]
        assert dataset.id == self.MINTED
        # the profile job spec's dataset_urn agrees with the event's URN
        assert c.runner.specs[0].dataset_urn == f"wr:{TENANT_A}:dataset:dataset/{self.MINTED}"
        # lineage edge targets a version URN of the SAME dataset id
        edges = list(state.edges.values())
        assert edges[0].to_urn == f"wr:{TENANT_A}:dataset:version/{self.MINTED}@v1"

    async def test_minted_id_honored_from_urn_alone(self, recording_container):
        """Legacy-shaped payload with dataset_urn but no explicit dataset_id."""
        c = recording_container
        await seed_snapshot(c)
        env = ingestion_envelope(
            TENANT_A, "ing-urn-only",
            dataset_urn=f"wr:{TENANT_A}:dataset:dataset/{self.MINTED}",
        )
        await c.bus.publish("ingestion.events.v1", env)
        assert list(c.memory_state.datasets.keys()) == [self.MINTED]

    async def test_second_ingestion_same_urn_appends_to_same_dataset(
        self, recording_container
    ):
        c = recording_container
        await seed_snapshot(c, snapshot_id=1001)
        await seed_snapshot(c, snapshot_id=1002)
        await c.bus.publish("ingestion.events.v1", self._env("ing-m1", snapshot_id=1001))
        await c.bus.publish("ingestion.events.v1", self._env("ing-m2", snapshot_id=1002))
        state = c.memory_state
        assert list(state.datasets.keys()) == [self.MINTED]
        assert sorted(v.version_no for v in state.versions.values()) == [1, 2]

    async def test_duplicate_delivery_of_minted_event_is_idempotent(
        self, recording_container
    ):
        c = recording_container
        await seed_snapshot(c)
        env = self._env("ing-dup")
        await c.bus.publish("ingestion.events.v1", env)
        await c.bus.publish("ingestion.events.v1", env)  # same event_id
        await c.bus.publish(  # broker re-key: new event_id, same ingestion
            "ingestion.events.v1", self._env("ing-dup")
        )
        state = c.memory_state
        assert len(state.datasets) == 1
        assert len(state.versions) == 1

    async def test_foreign_tenant_urn_is_ignored(self, recording_container):
        """A dataset_urn whose tenant differs from the envelope tenant must not
        drive the row id (MASTER-FR-003 defense in depth) — the consumer falls
        back to the name/create path with a locally minted id."""
        from tests.conftest import TENANT_B

        c = recording_container
        await seed_snapshot(c)
        env = ingestion_envelope(
            TENANT_A, "ing-foreign",
            dataset_urn=f"wr:{TENANT_B}:dataset:dataset/{self.MINTED}",
        )
        await c.bus.publish("ingestion.events.v1", env)
        state = c.memory_state
        assert len(state.datasets) == 1
        assert self.MINTED not in state.datasets


class TestIngestionFailed:
    async def test_marks_versionless_dataset_failed(self, recording_container):
        c = recording_container
        # dataset exists in draft with no versions
        from app.domain.services import CallCtx

        ds = await c.dataset_service.create(
            CallCtx(tenant_id=TENANT_A, actor={"type": "service", "id": "ingestion"}),
            {"workspace_id": "33333333-3333-4333-8333-333333333333", "name": "Doomed"},
        )
        env = ingestion_envelope(TENANT_A, "ing-f", event_type="ingestion.failed")
        env["payload"]["dataset_id"] = ds.id
        env["payload"]["error_digest"] = "boom"
        await c.bus.publish("ingestion.events.v1", env)
        stored = c.memory_state.datasets[ds.id]
        assert stored.status == "failed"
        assert stored.error_log == {"source": "ingestion", "digest": "boom"}

    async def test_does_not_fail_dataset_with_versions(self, recording_container):
        c = recording_container
        await seed_snapshot(c)
        await c.bus.publish(
            "ingestion.events.v1",
            ingestion_envelope(TENANT_A, "ing-ok", skip_profiling=True),
        )
        dataset = next(iter(c.memory_state.datasets.values()))
        env = ingestion_envelope(TENANT_A, "ing-f2", event_type="ingestion.failed")
        env["payload"]["dataset_id"] = dataset.id
        await c.bus.publish("ingestion.events.v1", env)
        assert c.memory_state.datasets[dataset.id].status == "ready"


class TestExactlyOnceOnError:
    async def test_handler_error_leaves_event_unmarked_then_redelivery_succeeds(
        self, recording_container
    ):
        """Handle-then-mark: if the handler raises, the event is NOT deduped, so a
        redelivery re-runs it and (handler being idempotent) lands correct exactly
        once — the setup real Kafka + DLQ needs (MASTER-FR-032)."""
        c = recording_container
        # No snapshot committed yet -> the handler's version registration fails
        # partway through (BR-1), raising out of handle() before the mark.
        env = ingestion_envelope(TENANT_A, "ing-err", snapshot_id=1001)

        try:
            await c.ingestion_handler.handle(env)
        except Exception:  # noqa: BLE001 - expected mid-handler failure
            pass

        # Event must remain un-deduped so redelivery can re-run it.
        assert not await c.dedup.already_processed(TENANT_A, env["event_id"])

        # Redeliver after the upstream data becomes readable -> succeeds once.
        await seed_snapshot(c, snapshot_id=1001)
        await c.ingestion_handler.handle(env)
        state = c.memory_state
        assert len(state.versions) == 1
        assert await c.dedup.already_processed(TENANT_A, env["event_id"])

        # A further duplicate now no-ops (marker present).
        await c.ingestion_handler.handle(env)
        assert len(state.versions) == 1

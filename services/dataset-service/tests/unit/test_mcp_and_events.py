"""Unit: MCP read facade (AC-14), event envelope (MASTER-FR-031), outbox order."""

from __future__ import annotations

import pytest

from app.domain.errors import ValidationFailed
from app.domain.services import CallCtx
from app.events.envelope import make_envelope
from tests.conftest import TENANT_A, create_dataset
from tests.unit.test_profiles_api import register_version

AGENT_CTX = CallCtx(
    tenant_id=TENANT_A,
    actor={"type": "user", "id": "user-1"},
    via_agent={"agent_id": "onboarding-agent", "version": "1.2.0"},
    trace_id="trace-mcp",
)


class TestMcpFacade:
    async def test_ac14_profile_summary_without_signed_urls_and_audited(
        self, client, container
    ):
        """AC-14: read-tier tool returns summary sans URLs; call audited."""
        ds = await create_dataset(client, name="McpDs")
        await register_version(client, container, ds)
        result = await container.mcp.get_dataset_profile(AGENT_CTX, ds["urn"])
        assert result["status"] == "completed"
        assert result["table"]["row_count"] == 100
        assert "full_json_url" not in result
        assert "html_report_url" not in result

        audits = container.memory_state.events_of_type("ai.tool_invoked.v1")
        assert len(audits) == 1
        assert audits[0]["payload"]["tool"] == "get_dataset_profile"
        assert audits[0]["via_agent"] == {"agent_id": "onboarding-agent",
                                          "version": "1.2.0"}

    async def test_search_and_schema_and_similar(self, client, container):
        ds = await create_dataset(client, name="McpSearch", tags=["gold"])
        await register_version(client, container, ds, skip_profiling=True,
                               schema={"customer_id": {"type": "long"}})
        hits = await container.mcp.search_datasets(AGENT_CTX, q="McpSearch")
        assert [h["name"] for h in hits] == ["McpSearch"]
        schema = await container.mcp.get_dataset_schema(AGENT_CTX, ds["urn"])
        assert "customer_id" in schema["schema"]
        similar = await container.mcp.find_similar_datasets(AGENT_CTX, ["customer_id"])
        assert similar and similar[0]["name"] == "McpSearch"

    async def test_lineage_depth_capped_at_5(self, container):
        with pytest.raises(ValidationFailed):
            await container.mcp.get_lineage(
                AGENT_CTX, f"wr:{TENANT_A}:dataset:dataset/x", depth=6
            )


class TestEnvelopeAndOutbox:
    def test_envelope_fields(self):
        env = make_envelope(
            event_type="dataset.created",
            tenant_id=TENANT_A,
            actor={"type": "user", "id": "u1"},
            resource_urn=f"wr:{TENANT_A}:dataset:dataset/d1",
            payload={"name": "x"},
            via_agent=None,
            trace_id="t-1",
        )
        assert set(env) == {
            "event_id", "event_type", "tenant_id", "actor", "via_agent",
            "resource_urn", "occurred_at", "trace_id", "payload",
        }
        assert env["event_type"] == "dataset.created"
        assert env["tenant_id"] == TENANT_A

    async def test_events_flushed_only_on_commit(self, container):
        """MASTER-FR-034: never emit before commit (memory analog)."""
        state = container.memory_state
        uow = container.deps.uow_factory(TENANT_A)
        async with uow:
            await uow.outbox.add("dataset.events.v1", {"event_type": "x", "payload": {}})
            assert state.outbox == []  # staged, not visible pre-commit
        assert len(state.outbox) == 1

    async def test_rollback_discards_events(self, container):
        state = container.memory_state
        uow = container.deps.uow_factory(TENANT_A)
        try:
            async with uow:
                await uow.outbox.add("dataset.events.v1", {"event_type": "y", "payload": {}})
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert state.outbox == []

    async def test_profile_events_carry_actor_and_urn(self, client, container):
        ds = await create_dataset(client, name="EvtDs")
        await register_version(client, container, ds)
        evt = container.memory_state.events_of_type("dataset.version_created")[0]
        assert evt["resource_urn"].startswith(f"wr:{TENANT_A}:dataset:version/")
        assert evt["actor"]["type"] == "service"
        assert evt["payload"]["version_no"] == 1

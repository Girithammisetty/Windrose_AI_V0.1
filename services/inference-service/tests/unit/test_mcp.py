"""MCP facade + agent-attribution tests (INF-FR-060, AC-14)."""

from __future__ import annotations

from app.domain.ports import CallCtx
from app.domain.services import SubmitRequest
from tests.conftest import TENANT_A, WORKSPACE, add_input_dataset

MODEL = f"wr:{TENANT_A}:experiment:model_version/fraud-xgb@3"
DS = f"wr:{TENANT_A}:dataset:dataset/ds-txn"


def _agent_ctx():
    return CallCtx(
        tenant_id=TENANT_A,
        actor={"type": "user", "id": "approver", "scopes": ["*"]},
        via_agent={"agent_id": "inference-agent", "version": "1.0"},
        workspace_id=WORKSPACE, submitted_by="approver")


async def test_ac14_proposal_carries_report_and_no_job_runs(container):
    add_input_dataset(container, urn=DS)
    proposal = await container.mcp.propose_job_submit(_agent_ctx(), MODEL, DS)
    assert proposal["proposal_type"] == "inference.job.create"
    assert proposal["requires_approval"] is True
    assert proposal["predicted_effect"]["compatible"] is True
    assert container.memory_state.jobs == {}  # nothing ran before approval


async def test_ac14_approved_job_records_via_agent(container):
    add_input_dataset(container, urn=DS)
    job = await container.inference.submit(_agent_ctx(), SubmitRequest(MODEL, DS))
    assert job.via_agent == {"agent_id": "inference-agent", "version": "1.0"}
    assert job.submitted_by == "approver"


async def test_compatibility_check_tool(container):
    add_input_dataset(container, urn=DS, schema={"amount": {"type": "double", "nullable": False}})
    report = await container.mcp.compatibility_check(_agent_ctx(), MODEL, DS)
    assert report["compatible"] is False  # missing age + merchant_id


async def test_read_tools_list(container):
    add_input_dataset(container, urn=DS)
    await container.inference.submit(_agent_ctx(), SubmitRequest(MODEL, DS))
    jobs = await container.mcp.jobs_list(_agent_ctx())
    assert len(jobs["jobs"]) == 1

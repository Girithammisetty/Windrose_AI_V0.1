"""Real Temporal durability (ART-FR-010, AC-2, NFR run-durability).

An AgentRunWorkflow runs on the REAL Temporal server against the REAL Postgres
store. The run pauses in awaiting_approval (durable HITL). We CANCEL the worker
(simulated crash), start a fresh worker, then approve — the workflow replays from
history and resumes to completion, issuing the signed grant. No lost run.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.adapters.fakes import FakeCaseReader, FakeLlm, FakeToolClient, NoopRealtime
from app.container import build_container
from app.domain.entities import Run, Session, new_uuid, now
from app.events.bus import InMemoryEventBus
from app.runtime.temporalx.worker import build_worker, connect
from tests.conftest import TENANT_A, make_settings

pytestmark = pytest.mark.integration


async def _wait(cond, timeout=20.0, interval=0.3):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await cond():
            return True
        await asyncio.sleep(interval)
    return False


async def test_run_survives_worker_restart(app_session_factory, require_temporal):
    from datetime import timedelta

    tq = f"agents-test-{uuid.uuid4().hex[:8]}"
    settings = make_settings(use_temporal=True, temporal_task_queue=tq)
    tool = FakeToolClient()
    c = build_container(
        settings, mode="sql", session_factory=app_session_factory,
        llm=FakeLlm(), tool_client=tool, bus=InMemoryEventBus(), realtime=NoopRealtime(),
        case_reader=FakeCaseReader())

    client = await connect(settings.temporal_target, settings.temporal_namespace)
    c.extras["temporal_client"] = client

    # session + run
    sess = Session(session_id=new_uuid(), tenant_id=TENANT_A, user_id="u-77",
                   agent_key="case-triage", agent_version=1, context_urn=None,
                   status="active", created_at=now(), last_activity_at=now(),
                   expires_hard_at=now() + timedelta(hours=8))
    await c.store.create_session(sess)
    run = Run(run_id=new_uuid(), tenant_id=TENANT_A, session_id=sess.session_id,
              agent_key="case-triage", agent_version=1,
              temporal_workflow_id=f"run:{new_uuid()}", status="queued",
              principal_type="user_obo", obo_sub="u-77")
    await c.store.create_run(run)

    req = {"tenant_id": TENANT_A, "run_id": run.run_id,
           "inputs": {"tenant_id": TENANT_A, "case_id": "c-91"},
           "obo_token": "tok", "obo_user": "u-77", "prompt_params": {},
           "auto_execute_policy": {}, "proposal_ttl_seconds": 3600}

    handle = await client.start_workflow(
        "AgentRunWorkflow", req, id=run.temporal_workflow_id, task_queue=tq)

    # worker #1 processes the graph + proposal, then the workflow blocks awaiting.
    w1 = build_worker(client, c, task_queue=tq)
    t1 = asyncio.create_task(w1.run())

    async def _pending():
        props = await c.store.list_proposals(TENANT_A, status="pending")
        return len(props) == 1
    assert await _wait(_pending), "proposal was not created"
    pid = (await c.store.list_proposals(TENANT_A, status="pending"))[0].proposal_id

    async def _awaiting():
        r = await c.store.get_run(TENANT_A, run.run_id)
        return r.status == "awaiting_approval"
    assert await _wait(_awaiting), "run did not reach awaiting_approval"

    # ---- simulate worker crash: cancel worker #1 while the run is paused ----
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    await asyncio.sleep(1.0)  # run is durably paused in Temporal, no worker alive

    # approve out-of-band (transition only; execution deferred to the workflow)
    decided = await c.proposal_service.decide(
        tenant_id=TENANT_A, proposal_id=pid, actor_sub="u-super", action="approve",
        execute=False)
    assert decided.status == "approved"

    # ---- fresh worker: the workflow replays from history and resumes ----
    w2 = build_worker(client, c, task_queue=tq)
    t2 = asyncio.create_task(w2.run())
    await handle.signal("proposal_decision",
                        {"action": "approve", "decided_by": "u-super",
                         "args": decided.args, "decided_at": now().isoformat()})

    result = await asyncio.wait_for(handle.result(), timeout=30)
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2

    assert result["status"] == "completed"
    # the signed grant was issued and presented to tool-plane during resume
    assert len(tool.calls) == 1 and tool.calls[0]["grant"] is not None
    r = await c.store.get_run(TENANT_A, run.run_id)
    assert r.status == "completed"

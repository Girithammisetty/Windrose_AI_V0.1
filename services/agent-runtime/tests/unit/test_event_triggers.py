"""Event-driven decisioning (real-time intelligence inc 1): a domain event fires a
GOVERNED autonomous agent run — and every governance gate still applies."""

from __future__ import annotations

from app.agents.catalog import seed_catalog
from app.container import build_container
from app.domain.entities import KillSwitch, TenantAgentConfig
from app.runtime.event_triggers import EventTriggerDispatcher
from tests.conftest import TENANT_A, make_settings


async def _container():
    c = build_container(make_settings(), mode="memory")
    await seed_catalog(c.store, c.signing_key)
    return c


def _event(**kw):
    """The REAL master envelope (MASTER-FR-031): body under `payload`, subject
    under top-level `resource_urn` — not a hand-rolled shape."""
    base = {"event_id": "evt-1", "event_type": "case.created", "tenant_id": TENANT_A,
            "actor": {"type": "user", "id": "u-1"}, "via_agent": None,
            "resource_urn": f"wr:{TENANT_A}:case:case/c-91",
            "occurred_at": "2026-07-19T00:00:00Z", "trace_id": "t-1",
            "payload": {"case_id": "c-91"}}
    base.update(kw)
    return base


async def test_mapped_event_fires_a_governed_autonomous_run():
    c = await _container()
    out = await EventTriggerDispatcher(c).handle(_event())
    assert out.fired is True and out.agent_key == "case-triage"
    run = await c.store.get_run(TENANT_A, out.run_id) if hasattr(c.store, "get_run") else None
    if run is not None:
        assert run.principal_type == "agent_autonomous"  # no human in the loop to start it


async def test_trigger_provenance_is_recorded_on_the_run_inputs():
    c = await _container()
    fired = []
    orig = c.run_engine.execute

    async def spy(run, inputs, **kw):
        fired.append(inputs)
        return await orig(run, inputs, **kw)

    c.run_engine.execute = spy
    await EventTriggerDispatcher(c).handle(_event())
    assert fired, "run engine was never invoked"
    inputs = fired[0]
    assert inputs["case_id"] == "c-91"                       # envelope `payload` carried
    assert inputs["trigger"]["event_type"] == "case.created"  # why it ran (auditable)
    assert inputs["trigger"]["event_id"] == "evt-1"
    assert inputs["trigger"]["resource_urn"].endswith("case/c-91")


async def test_accepts_the_data_alias_for_simplified_events():
    """A hand-published event using `data` instead of `payload` still works."""
    c = await _container()
    evt = _event(payload=None, data={"case_id": "c-77"})
    out = await EventTriggerDispatcher(c).handle(evt)
    assert out.fired is True


async def test_unmapped_event_type_does_not_fire():
    c = await _container()
    out = await EventTriggerDispatcher(c).handle(_event(event_type="case.commented"))
    assert out.fired is False and out.reason == "no_trigger_for_event_type"


async def test_malformed_envelope_is_skipped_not_raised():
    c = await _container()
    d = EventTriggerDispatcher(c)
    assert (await d.handle({})).reason == "malformed_envelope"
    assert (await d.handle({"event_type": "case.created"})).reason == "malformed_envelope"
    assert (await d.handle("not-a-dict")).reason == "malformed_envelope"


async def test_agent_disabled_for_tenant_is_not_auto_triggered():
    c = await _container()
    await c.store.put_tenant_config(TenantAgentConfig(
        tenant_id=TENANT_A, agent_key="case-triage", enabled=False))
    out = await EventTriggerDispatcher(c).handle(_event())
    assert out.fired is False and out.reason == "agent_disabled_for_tenant"


async def test_kill_switch_blocks_event_triggered_runs():
    c = await _container()
    await c.kill_registry.set_kill(KillSwitch(
        kill_id="k-1", scope="agent", agent_key="case-triage", version=None,
        tenant_id=TENANT_A, active=True, reason="incident", set_by="op-1"))
    out = await EventTriggerDispatcher(c).handle(_event())
    assert out.fired is False and out.reason == "agent_killed"

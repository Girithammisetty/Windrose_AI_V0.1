"""Scheduled drift-driven retrain loop (BRD 52 inc3). The scheduler counts human
corrections to a watched agent's proposals and, over threshold, invokes the
governance agent (which opens a four-eyes retrain proposal). Proves: it triggers
over threshold, stays quiet under it, and records the signal on the watch."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import app.runtime.retrain_scheduler as mod
from app.domain.entities import RetrainWatch
from app.store.memory import InMemoryStore
from tests.conftest import TENANT_A

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _prop(status: str, agent_key: str = "case-triage", tenant: str = TENANT_A):
    return SimpleNamespace(tenant_id=tenant, agent_key=agent_key, status=status)


async def _store_with(watch: RetrainWatch, proposals: list) -> InMemoryStore:
    store = InMemoryStore()
    await store.create_retrain_watch(watch)
    for i, p in enumerate(proposals):
        store._proposals[f"p-{i}"] = p
    return store


def _patched_orch(monkeypatch, recorded: list):
    class _FakeOrch:
        def __init__(self, c):
            pass

        async def get_or_create_session(self, **kw):
            return SimpleNamespace(session_id="s-1", tenant_id=kw["tenant_id"], agent_version=1)

        async def start_run(self, **kw):
            recorded.append(kw)
            return SimpleNamespace(run_id="r-1"), {}

    monkeypatch.setattr(mod, "Orchestrator", _FakeOrch)


async def test_triggers_governance_over_correction_threshold(monkeypatch):
    watch = RetrainWatch(id="w1", tenant_id=TENANT_A, model_urn="wr:t:experiment:model/m",
                         watched_agent_key="case-triage", min_corrections=2, drift_threshold=0.99)
    store = await _store_with(watch, [
        _prop("rejected"), _prop("edited_approved"), _prop("approved"),
        _prop("approved", agent_key="other"),  # different agent — ignored
    ])
    recorded: list = []
    _patched_orch(monkeypatch, recorded)

    n = await mod.RetrainScheduler(SimpleNamespace(store=store)).tick(now=_NOW)

    assert n == 1
    assert recorded[0]["agent_key"] == "governance"
    assert recorded[0]["principal_type"] == "agent_autonomous"
    assert recorded[0]["inputs"]["signals"]["corrections"] == 2
    assert recorded[0]["inputs"]["model_urn"] == "wr:t:experiment:model/m"
    # The signal is recorded on the watch, last_checked advanced.
    w = (await store.list_retrain_watches(TENANT_A))[0]
    assert w.last_checked_at == _NOW
    assert w.last_signal["triggered"] is True


async def test_quiet_under_threshold(monkeypatch):
    watch = RetrainWatch(id="w2", tenant_id=TENANT_A, model_urn="wr:t:experiment:model/m",
                         watched_agent_key="case-triage", min_corrections=5, drift_threshold=0.9)
    store = await _store_with(watch, [_prop("rejected"), _prop("approved"), _prop("approved")])
    recorded: list = []
    _patched_orch(monkeypatch, recorded)

    n = await mod.RetrainScheduler(SimpleNamespace(store=store)).tick(now=_NOW)

    assert n == 0
    assert recorded == []                       # governance never invoked
    w = (await store.list_retrain_watches(TENANT_A))[0]
    assert w.last_checked_at == _NOW            # still checked (cadence advances)
    assert w.last_signal["triggered"] is False


async def test_drift_ratio_triggers_even_below_min_corrections(monkeypatch):
    # 1 correction of 1 decided -> drift 1.0 >= 0.3 threshold, even though
    # min_corrections (20) is not met. Either signal opens the proposal.
    watch = RetrainWatch(id="w3", tenant_id=TENANT_A, model_urn="wr:t:experiment:model/m",
                         watched_agent_key="case-triage", min_corrections=20, drift_threshold=0.3)
    store = await _store_with(watch, [_prop("rejected")])
    recorded: list = []
    _patched_orch(monkeypatch, recorded)

    n = await mod.RetrainScheduler(SimpleNamespace(store=store)).tick(now=_NOW)
    assert n == 1
    assert recorded[0]["inputs"]["signals"]["drift_score"] == 1.0

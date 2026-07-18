"""Scheduled, drift-driven retrain loop (BRD 52 inc3 / Phase 3 / WS3).

A background worker that, on each watch's cadence, computes a REAL drift signal —
the rate at which humans are CORRECTING the watched agent's proposals (rejected or
edited-before-approval) in a recent window — and, when it crosses the watch's
threshold, invokes the governance agent AUTONOMOUSLY. The governance graph decides
whether to open a four-eyes ``mlops.open_retrain`` PROPOSAL; a human still approves
every retrain. The loop only ever PROPOSES — never retrains or deploys on its own.

The drift signal is intentionally simple and honest: corrections are ground-truth
human overrides already recorded on proposals. Richer model-performance drift
(realized-vs-predicted from outcome monitoring) is a later refinement.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.runtime.orchestrator import Orchestrator

logger = logging.getLogger("agent-runtime.retrain")

GOVERNANCE_AGENT = "governance"


def _now() -> datetime:
    return datetime.now(UTC)


class RetrainScheduler:
    def __init__(self, container, *, interval_seconds: float = 300.0) -> None:
        self._c = container
        self._interval = interval_seconds

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Loop until cancelled/stopped. Each tick is best-effort and NEVER fatal —
        a failing watch is logged and the others still run."""
        while stop is None or not stop.is_set():
            try:
                await self.tick()
            except Exception:  # pragma: no cover - defensive
                logger.exception("retrain scheduler tick failed")
            if stop is None:
                await asyncio.sleep(self._interval)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._interval)
                    return  # stop signalled
                except TimeoutError:
                    pass

    async def tick(self, now: datetime | None = None) -> int:
        """Process all due watches once. Returns the number of watches that
        triggered a governance run (drift over threshold)."""
        now = now or _now()
        due = await self._c.store.list_due_retrain_watches(now)
        triggered = 0
        for w in due:
            signal = await self._signal_for(w, now)
            over = (signal["corrections"] >= w.min_corrections
                    or signal["drift_score"] >= w.drift_threshold)
            if over:
                await self._invoke_governance(w, signal)
                triggered += 1
            await self._c.store.touch_retrain_watch(w.id, now, {**signal, "triggered": over})
        if due:
            logger.info("retrain scheduler: %d due, %d triggered", len(due), triggered)
        return triggered

    async def _signal_for(self, w, now: datetime) -> dict:
        since = now - timedelta(hours=w.correction_window_hours)
        corrections, total = await self._c.store.count_corrections(
            w.tenant_id, w.watched_agent_key, since)
        drift = (corrections / total) if total else 0.0
        return {"corrections": corrections, "total": total, "drift_score": round(drift, 4)}

    async def _invoke_governance(self, w, signal: dict) -> None:
        orch = Orchestrator(self._c)
        session = await orch.get_or_create_session(
            tenant_id=w.tenant_id, user_id=None, agent_key=GOVERNANCE_AGENT,
            session_id=None, context_urn=w.model_urn)
        inputs = {
            "model_urn": w.model_urn,
            "workspace_id": w.workspace_id,
            "signals": {"drift_score": signal["drift_score"], "corrections": signal["corrections"]},
            "drift_threshold": w.drift_threshold,
            "trigger": "scheduled_drift_watch",
        }
        await orch.start_run(principal=None, agent_key=GOVERNANCE_AGENT, inputs=inputs,
                             session=session, principal_type="agent_autonomous")
        logger.info("retrain scheduler: invoked governance for %s (corrections=%d, drift=%s)",
                    w.model_urn, signal["corrections"], signal["drift_score"])

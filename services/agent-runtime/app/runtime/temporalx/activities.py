"""Temporal activities (ART-FR-010): LangGraph node/graph execution + proposal
lifecycle run as activities so the workflow stays deterministic and durable.

Activities are instance methods bound to the container's REAL adapters; all IO
(LLM via ai-gateway, tool-plane, Postgres) happens here, never in the workflow.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from types import SimpleNamespace

from temporalio import activity

from app.graphs.base import WriteIntent

log = logging.getLogger("agent_runtime.activities")


class AgentActivities:
    def __init__(self, container) -> None:
        self._c = container

    @activity.defn(name="run_graph")
    async def run_graph(self, req: dict) -> dict:
        run = await self._c.store.get_run(req["tenant_id"], req["run_id"])
        run.status = "running"
        await self._c.store.update_run(run)
        await self._c.run_engine.emit_run(run, "agent_run.started")
        outcome = await self._c.run_engine.run_graph(
            run, req["inputs"], obo_token=req.get("obo_token"),
            prompt_params=req.get("prompt_params", {}))
        # checkpoint the super-step (ART-FR-010)
        await self._c.store.save_checkpoint(
            tenant_id=run.tenant_id, run_id=run.run_id, checkpoint_id="graph-final",
            seq=1, state_ref={"usage": outcome.usage, "trace": outcome.trace})
        wi = asdict(outcome.write_intent) if outcome.write_intent else None
        # evidence travels so capture_transcript can record grounding (the inline
        # path reads outcome.evidence; the Temporal path serializes it here).
        return {"final_text": outcome.final_text, "write_intent": wi,
                "usage": outcome.usage, "trace": outcome.trace,
                "evidence": outcome.evidence}

    @activity.defn(name="capture_transcript")
    async def capture_transcript(self, req: dict, outcome: dict,
                                 proposal_id: str | None) -> dict:
        """SLM distillation (ART, task #72): capture the completed run into the
        governed agent_transcripts corpus on the TEMPORAL path too — the inline
        engine does this at engine.py:166, but the workflow uses run_graph (not
        execute), so without this every Temporal-backed run (the default) yielded
        ZERO transcripts and ProposalService.decide's attach_decision silently
        no-op'd, losing the (input -> corrected-output) training pair. Strictly
        best-effort: a capture failure NEVER fails the run (consent + PII
        redaction are enforced inside TranscriptSink.capture)."""
        if self._c.transcripts is None:
            return {"captured": False, "reason": "sink_disabled"}
        try:
            run = await self._c.store.get_run(req["tenant_id"], req["run_id"])
            if run is None:
                return {"captured": False, "reason": "run_missing"}
            wi_dict = outcome.get("write_intent")
            shim = SimpleNamespace(
                final_text=outcome.get("final_text"),
                usage=outcome.get("usage", {}) or {},
                evidence=outcome.get("evidence", []) or [],
                write_intent=WriteIntent(**wi_dict) if wi_dict else None)
            await self._c.transcripts.capture(
                run, req.get("inputs", {}), shim, proposal_id)
            return {"captured": True}
        except Exception:  # noqa: BLE001 — never fail the run for a capture problem
            log.warning("capture_transcript failed for run %s",
                        req.get("run_id"), exc_info=True)
            return {"captured": False, "reason": "error"}

    @activity.defn(name="create_proposal")
    async def create_proposal(self, req: dict, intent: dict) -> dict:
        run = await self._c.store.get_run(req["tenant_id"], req["run_id"])
        wi = WriteIntent(**intent)
        prop, executed = await self._c.proposal_service.create_from_intent(
            run=run, intent=wi, obo_user=req.get("obo_user"),
            auto_execute_policy=req.get("auto_execute_policy", {}))
        return {"proposal_id": prop.proposal_id, "executed": executed,
                "status": prop.status, "expires_at": prop.expires_at.isoformat()}

    @activity.defn(name="execute_proposal")
    async def execute_proposal(self, tenant_id: str, proposal_id: str, decided_by: str,
                               args: dict) -> dict:
        prop = await self._c.store.get_proposal(tenant_id, proposal_id)
        result = await self._c.proposal_service.execute_approved(
            prop, decided_by=decided_by, args=args or prop.args)
        return {"ok": result.ok, "status": result.status, "code": result.code}

    @activity.defn(name="mark_awaiting")
    async def mark_awaiting(self, tenant_id: str, run_id: str,
                            final_text: str | None = None) -> None:
        run = await self._c.store.get_run(tenant_id, run_id)
        run.status = "awaiting_approval"
        if final_text is not None:
            run.final_text = final_text
        await self._c.store.update_run(run)
        await self._c.run_engine.emit_run(run, "agent_run.state_changed",
                                          payload={"awaiting": True})
        # Deliver the answer to the chat stream now — the HITL wait can last
        # days; the subscriber must not hang until the decision (mirrors the
        # inline engine, which streams the final text before parking).
        await self._c.run_engine.publish_final_stream(run, run.final_text)

    @activity.defn(name="finalize_run")
    async def finalize_run(self, tenant_id: str, run_id: str, status: str,
                           usage: dict, final_text: str | None = None) -> None:
        """Terminal step: persist status + usage + the FINAL ANSWER TEXT, emit
        the terminal Kafka event, and deliver the answer to hub subscribers
        (token -> run_completed -> done). Without this the chat answer computed
        by run_graph would be discarded by the workflow."""
        run = await self._c.store.get_run(tenant_id, run_id)
        run.status = status
        run.usage = usage or run.usage
        if final_text is not None:
            run.final_text = final_text
        await self._c.store.update_run(run)
        await self._c.run_engine.emit_run(run, "agent_run.completed")
        await self._c.run_engine.publish_final_stream(run, run.final_text)

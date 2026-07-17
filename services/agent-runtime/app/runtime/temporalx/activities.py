"""Temporal activities (ART-FR-010): LangGraph node/graph execution + proposal
lifecycle run as activities so the workflow stays deterministic and durable.

Activities are instance methods bound to the container's REAL adapters; all IO
(LLM via ai-gateway, tool-plane, Postgres) happens here, never in the workflow.
"""

from __future__ import annotations

from dataclasses import asdict

from temporalio import activity

from app.graphs.base import WriteIntent


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
        return {"final_text": outcome.final_text, "write_intent": wi,
                "usage": outcome.usage, "trace": outcome.trace}

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

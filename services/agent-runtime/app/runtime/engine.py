"""Run engine (ART-FR-010/011): drives a LangGraph run and converts a graph
WriteIntent into a Proposal (never a direct write). Used both by the chat API
(when Temporal is off) and by the Temporal ``run_graph``/``finalize_run``
activities (graph step / completion). Emits on ``ai.agent_run.v1`` at every
state transition with semantic event_types (``agent_run.started`` /
``agent_run.state_changed`` / ``agent_run.completed``) matching the Go
consumers (usage-service ingest mapping keys on event_type
``agent_run.completed``; audit-service validates the master envelope)."""

from __future__ import annotations

from app.constants import TOPIC_AGENT_RUN
from app.domain.entities import Run
from app.domain.urn import run_urn
from app.events.envelope import make_envelope
from app.graphs import RUNNERS, GraphDeps


class RunEngine:
    def __init__(self, *, store, proposals, bus, realtime, llm, memory, case_reader,
                 settings, ingestion_reader=None, experiment_reader=None,
                 dataset_reader=None, pipeline_reader=None, semantic_reader=None,
                 catalog_reader=None, transcripts=None) -> None:
        self._store = store
        self._proposals = proposals
        self._bus = bus
        self._rt = realtime
        self._llm = llm
        self._memory = memory
        self._case_reader = case_reader
        # SLM distillation milestone 1: best-effort transcript capture (None → off).
        self._transcripts = transcripts
        self._ingestion_reader = ingestion_reader
        self._experiment_reader = experiment_reader
        self._dataset_reader = dataset_reader
        self._pipeline_reader = pipeline_reader
        self._semantic_reader = semantic_reader
        self._catalog_reader = catalog_reader
        self._settings = settings

    def _deps(self, run: Run, obo_token: str | None, prompt_params: dict) -> GraphDeps:
        return GraphDeps(llm=self._llm, memory=self._memory, case_reader=self._case_reader,
                         ingestion_reader=self._ingestion_reader,
                         experiment_reader=self._experiment_reader,
                         dataset_reader=self._dataset_reader,
                         pipeline_reader=self._pipeline_reader,
                         semantic_reader=self._semantic_reader,
                         catalog_reader=self._catalog_reader,
                         prompt_params=prompt_params or {}, obo_token=obo_token)

    async def run_graph(self, run: Run, inputs: dict, *, obo_token: str | None,
                        prompt_params: dict):
        _, runner = RUNNERS[run.agent_key]
        deps = self._deps(run, obo_token, prompt_params)
        return await runner(deps, inputs)

    async def replay(self, *, agent_key: str, inputs: dict, obo_token: str | None,
                     prompt_params: dict, memory_snapshot_ver: str | None):
        """ART-FR-015 replay / no-side-effect mode.

        Runs the REAL agent graph for the given case/inputs — real ai-gateway LLM
        call, real case read, real snapshot-pinned memory RAG read — but executes
        NOTHING that mutates state: no Run/Session/Proposal rows are created, no
        events are emitted, and the graph's WriteIntent is RETURNED as data
        (captured-not-executed) rather than converted into a Proposal. The graphs
        never call write tools directly (they only emit a WriteIntent that the
        engine would turn into a Proposal), so declining to create the proposal is
        what makes replay side-effect-free; ``deps.replay`` additionally pins RAG
        reads and marks the run for any adapter that honours the flag.

        Returns the graph :class:`GraphOutcome` (final answer + captured intent +
        structured disposition + grounding evidence + usage/trace)."""
        if agent_key not in RUNNERS:
            from app.domain.errors import NotFound
            raise NotFound(f"agent {agent_key} is not runnable")
        _, runner = RUNNERS[agent_key]
        deps = GraphDeps(
            llm=self._llm, memory=self._memory, case_reader=self._case_reader,
            ingestion_reader=self._ingestion_reader,
            experiment_reader=self._experiment_reader,
            dataset_reader=self._dataset_reader,
            pipeline_reader=self._pipeline_reader,
            semantic_reader=self._semantic_reader,
            catalog_reader=self._catalog_reader,
            prompt_params=prompt_params or {}, obo_token=obo_token,
            replay=True, memory_snapshot_ver=memory_snapshot_ver)
        return await runner(deps, dict(inputs))

    async def emit_run(self, run: Run, event_type: str, *, payload: dict | None = None) -> None:
        actor = ({"type": "agent", "id": run.agent_key}
                 if run.principal_type == "agent_autonomous"
                 else {"type": "user", "id": run.obo_sub or "unknown"})
        # usage-service meters agent tasks only when the terminal
        # agent_run.completed payload carries status == "succeeded"
        # (usage-service internal/ingest/mapping.go); our domain terminal
        # success status is "completed", so translate on the wire.
        wire_status = "succeeded" if run.status == "completed" else run.status
        env = make_envelope(
            event_type=event_type, tenant_id=run.tenant_id, actor=actor,
            via_agent={"agent_id": run.agent_key, "version": str(run.agent_version)},
            resource_urn=run_urn(run.tenant_id, run.run_id),
            payload={"run_id": run.run_id, "session_id": run.session_id,
                     "agent_key": run.agent_key, "agent_version": run.agent_version,
                     "principal_type": run.principal_type, "status": wire_status,
                     "run_status": run.status, **(payload or {})})
        await self._store.enqueue_outbox(tenant_id=run.tenant_id, topic=TOPIC_AGENT_RUN,
                                         envelope=env)
        await self._bus.publish(TOPIC_AGENT_RUN, env)

    async def publish_final_stream(self, run: Run, final_text: str | None) -> None:
        """Deliver the answer to subscribers on ``agent_run:{run_id}``: the full
        final text as one ``token`` chunk (v1 streaming), then ``run_completed``
        carrying the final text, then ``done`` so the ui hook closes the stream
        (ui-web useCopilotThread appends any data.type containing "token" and
        closes on a type containing "done")."""
        topic = f"agent_run:{run.run_id}"
        if final_text:
            await self._rt.publish(topic=topic, event="token",
                                   data={"text": final_text},
                                   tenant_id=run.tenant_id)
        await self._rt.publish(
            topic=topic, event="run_completed",
            data={"final_text": final_text, "usage": run.usage, "status": run.status},
            tenant_id=run.tenant_id)
        await self._rt.publish(topic=topic, event="done", data={},
                               tenant_id=run.tenant_id)

    async def execute(self, run: Run, inputs: dict, *, obo_token: str | None,
                      obo_user: str | None, prompt_params: dict,
                      auto_execute_policy: dict) -> dict:
        """Full non-Temporal run: graph -> (proposal|final). Returns a summary."""
        run.status = "running"
        await self._store.update_run(run)
        await self.emit_run(run, "agent_run.started")

        outcome = await self.run_graph(run, inputs, obo_token=obo_token,
                                       prompt_params=prompt_params)
        run.usage = outcome.usage or {}
        run.final_text = outcome.final_text
        summary: dict = {"final_text": outcome.final_text, "usage": run.usage,
                         "trace": outcome.trace}

        if outcome.write_intent is not None:
            prop, executed = await self._proposals.create_from_intent(
                run=run, intent=outcome.write_intent, obo_user=obo_user,
                auto_execute_policy=auto_execute_policy)
            summary["proposal_id"] = prop.proposal_id
            summary["proposal_status"] = prop.status
            if executed:
                run.status = "completed"
            else:
                run.status = "awaiting_approval"
                await self.emit_run(run, "agent_run.state_changed",
                                    payload={"awaiting": prop.proposal_id})
        else:
            run.status = "completed"

        await self._store.update_run(run)
        await self.emit_run(run, "agent_run.completed" if run.status == "completed"
                            else "agent_run.state_changed")
        await self.publish_final_stream(run, outcome.final_text)
        # SLM distillation: capture the run into the governed corpus (best-effort;
        # never fails the run). The human decision is joined in later by
        # ProposalService.decide when the proposal is approved/edited/rejected.
        if self._transcripts is not None:
            await self._transcripts.capture(run, inputs, outcome, summary.get("proposal_id"))
        return summary

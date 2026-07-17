"""MCP facade (INF-FR-060): read tools + write-proposal tools for agents.

Read tools resolve directly. Write tools (``inference.job.create``,
``inference.schedule.create/update``) return **proposals** carrying the
compatibility report as ``predicted_effect`` — they never auto-execute (the tool
plane requires human approval; MASTER §2). ``create_unpromoted`` is excluded from
the agent toolset (BR-2).
"""

from __future__ import annotations

from app.domain.enums import status_name
from app.domain.ports import CallCtx, Filters
from app.domain.schedules import ScheduleService
from app.domain.services import InferenceService, SubmitRequest


class McpFacade:
    def __init__(self, inference: InferenceService, schedules: ScheduleService):
        self.inference = inference
        self.schedules = schedules

    # ---- read tools ----

    async def jobs_list(self, ctx: CallCtx, *, limit: int = 50, cursor: str | None = None):
        page = await self.inference.list(ctx, Filters(), "-created_at", limit, cursor)
        return {"jobs": [j.id for j in page.items], "next_cursor": page.next_cursor}

    async def jobs_get(self, ctx: CallCtx, job_id: str):
        return await self.inference.get(ctx, job_id)

    async def compatibility_check(self, ctx: CallCtx, model_version_urn: str,
                                  input_dataset_urn: str) -> dict:
        req = SubmitRequest(model_version_urn=model_version_urn,
                            input_dataset_urn=input_dataset_urn)
        return await self.inference.validate(ctx, req)

    async def schedules_list(self, ctx: CallCtx, *, limit: int = 50, cursor: str | None = None):
        page = await self.schedules.list(ctx, limit, cursor)
        return {"schedules": [s.id for s in page.items], "next_cursor": page.next_cursor}

    # ---- write-proposal tools (never auto-execute) ----

    async def propose_job_submit(self, ctx: CallCtx, model_version_urn: str,
                                 input_dataset_urn: str, **params) -> dict:
        # allow_unpromoted is stripped for agents (toolset exclusion, BR-2)
        report = await self.compatibility_check(ctx, model_version_urn, input_dataset_urn)
        return {
            "proposal_type": "inference.job.create",
            "inputs": {"model_version_urn": model_version_urn,
                       "input_dataset_urn": input_dataset_urn, "parameters": params},
            "predicted_effect": report,
            "requires_approval": True,
        }

    async def propose_schedule_create(self, ctx: CallCtx, body: dict) -> dict:
        return {
            "proposal_type": "inference.schedule.create",
            "inputs": body,
            "requires_approval": True,
        }

    async def submit(self, ctx: CallCtx, model_version_urn: str, input_dataset_urn: str,
                     output_dataset_name: str | None = None,
                     parameters: dict | None = None, **_ignored) -> dict:
        """``inference.submit`` — the write-proposal EXECUTION path a batch-inference
        proposal binds to. The tool plane invokes this ONLY after a human approves
        the agent's proposal (never auto-executes; MASTER §2), presenting the signed
        proposal-execution grant; it runs inference-service's real submit path
        (INF-FR-001/002 — validate compatibility, create the job, launch scoring).
        ``allow_unpromoted`` is not accepted here (agents are toolset-excluded from
        scoring unpromoted models, BR-2). Extra readable args carried on the proposal
        (model_id/model_version/input_dataset) are ignored in favour of the canonical
        URNs. Returns the created job summary."""
        output = {"dataset_name": output_dataset_name} if output_dataset_name else None
        req = SubmitRequest(
            model_version_urn=model_version_urn, input_dataset_urn=input_dataset_urn,
            parameters=parameters or {}, output=output)
        job = await self.inference.submit(ctx, req)
        return {
            "job_id": job.id,
            "status": status_name(job.status),
            "model_version_urn": job.model_version_urn,
            "input_dataset_urn": job.input_dataset_urn,
            "output_dataset_name": job.output_dataset_name,
            "compatibility_report": job.compatibility_report,
        }

"""MCP facade (PIPE-FR-053, master §2.2-015). Read tools + write-proposal tools for
the model-training agent. Agent-filled templates are validated identically to UI
submissions; write tools are proposal-gated by the caller (master §8.5)."""

from __future__ import annotations

from app.api.schemas import (
    component_payload,
    run_payload,
    template_payload,
)


class McpFacade:
    READ_TOOLS = ["pipeline.components.list", "pipeline.templates.get",
                  "pipeline.runs.get"]
    WRITE_PROPOSAL_TOOLS = ["pipeline.template.create_from_algorithm",
                            "pipeline.run.submit"]

    def __init__(self, catalog, templates, runs, instantiation):
        self.catalog = catalog
        self.templates = templates
        self.runs = runs
        self.instantiation = instantiation

    def tools(self) -> dict:
        return {"read": self.READ_TOOLS, "write_proposal": self.WRITE_PROPOSAL_TOOLS}

    # ---- read ----
    async def components_list(self, ctx) -> list[dict]:
        return [component_payload(c) for c in self.catalog.list_components()]

    async def templates_get(self, ctx, template_id) -> dict:
        template, version = await self.templates.get(ctx, template_id)
        return template_payload(template, version)

    async def runs_get(self, ctx, run_id) -> dict:
        return run_payload(await self.runs.get(ctx, run_id))

    # ---- write-proposal (validated identically to UI) ----
    async def template_create_from_algorithm(self, ctx, *, algorithm, mode, dataset_refs,
                                             params, workspace_id, name=None) -> dict:
        template, version = await self.instantiation.instantiate_pipeline(
            ctx, algorithm, mode=mode, dataset_refs=dataset_refs, params=params,
            workspace_id=workspace_id, name=name)
        return template_payload(template, version)

    async def run_submit(self, ctx, *, template_id, run_parameters) -> dict:
        _, run = await self.runs.create_run(ctx, template_id, run_parameters)
        return run_payload(run)

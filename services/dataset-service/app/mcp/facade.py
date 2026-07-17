"""MCP read-only tool facade (BRD §5, AC-14).

Agents access dataset-service exclusively through these governed tools. All
tools are read tier: summaries only, never signed URLs; every invocation emits
an `ai.tool_invoked.v1` audit event. The MCP transport (gateway registration)
is owned by the tool-plane; this module is the callable surface it binds to.
"""

from __future__ import annotations

from app.domain.errors import ValidationFailed
from app.domain.ports import DatasetFilters
from app.domain.services import (
    CallCtx,
    DatasetService,
    LineageService,
    ProfileService,
    VersionService,
)
from app.domain.urn import parse_urn
from app.events.envelope import make_envelope

MCP_MAX_LINEAGE_DEPTH = 5


class McpFacade:
    def __init__(
        self,
        dataset_service: DatasetService,
        version_service: VersionService,
        profile_service: ProfileService,
        lineage_service: LineageService,
    ):
        self.datasets = dataset_service
        self.versions = version_service
        self.profiles = profile_service
        self.lineage = lineage_service
        self.deps = dataset_service.deps

    async def _audit_tool(self, ctx: CallCtx, tool: str, args: dict) -> None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            await uow.outbox.add(
                self.deps.settings.events_topic,
                make_envelope(
                    event_type="ai.tool_invoked.v1",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    via_agent=ctx.via_agent,
                    resource_urn=f"wr:{ctx.tenant_id}:dataset:tool/{tool}",
                    payload={"tool": tool, "args": args},
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()

    def _dataset_id(self, ctx: CallCtx, urn: str) -> str:
        parsed = parse_urn(urn)
        if parsed.service != "dataset" or parsed.rtype != "dataset":
            raise ValidationFailed("expected a dataset URN")
        return parsed.rid

    async def search_datasets(self, ctx: CallCtx, q: str | None = None,
                              filters: dict | None = None) -> list[dict]:
        await self._audit_tool(ctx, "search_datasets", {"q": q, "filters": filters})
        filters = filters or {}
        page = await self.datasets.list(
            ctx,
            DatasetFilters(
                q=q,
                status=filters.get("status"),
                tags=filters.get("tags") or [],
                column=filters.get("column"),
                quality_flag=filters.get("quality_flag"),
                has_pii=filters.get("has_pii"),
            ),
            "-created_at", 50, None,
        )
        return [
            {"urn": f"wr:{ctx.tenant_id}:dataset:dataset/{d.id}", "name": d.name,
             "status": str(d.status), "tags": d.tags}
            for d in page.items
        ]

    async def get_dataset(self, ctx: CallCtx, urn: str) -> dict:
        await self._audit_tool(ctx, "get_dataset", {"urn": urn})
        dataset, current = await self.datasets.get(ctx, self._dataset_id(ctx, urn))
        return {
            "urn": urn, "name": dataset.name, "status": str(dataset.status),
            "lifecycle": str(dataset.lifecycle), "tags": dataset.tags,
            "current_version_no": current.version_no if current else None,
            "row_count": current.row_count if current else None,
        }

    async def get_dataset_schema(self, ctx: CallCtx, urn: str,
                                 version: int | None = None) -> dict:
        await self._audit_tool(ctx, "get_dataset_schema", {"urn": urn, "version": version})
        dataset_id = self._dataset_id(ctx, urn)
        if version is not None:
            v = await self.versions.get(ctx, dataset_id, version)
            return {"version_no": v.version_no, "schema": v.schema}
        dataset, current = await self.datasets.get(ctx, dataset_id)
        return {
            "version_no": current.version_no if current else None,
            "schema": current.schema if current else {},
        }

    async def get_dataset_profile(self, ctx: CallCtx, urn: str,
                                  version: int | None = None) -> dict:
        """Summary only — NO signed URLs at read tier (AC-14)."""
        await self._audit_tool(ctx, "get_dataset_profile", {"urn": urn, "version": version})
        summary = await self.profiles.get_summary(ctx, self._dataset_id(ctx, urn), version)
        summary.pop("full_json_url", None)
        summary.pop("html_report_url", None)
        return summary

    async def get_lineage(self, ctx: CallCtx, urn: str, direction: str = "both",
                          depth: int = 3) -> dict:
        await self._audit_tool(ctx, "get_lineage", {"urn": urn, "direction": direction,
                                                    "depth": depth})
        if depth > MCP_MAX_LINEAGE_DEPTH:
            raise ValidationFailed(f"MCP lineage depth capped at {MCP_MAX_LINEAGE_DEPTH}")
        return await self.lineage.query(ctx, urn=urn, direction=direction, depth=depth,
                                        activities=None)

    async def find_similar_datasets(self, ctx: CallCtx, columns: list[str]) -> list[dict]:
        await self._audit_tool(ctx, "find_similar_datasets", {"columns": columns})
        return await self.datasets.similar(ctx, schema=None, columns=columns)

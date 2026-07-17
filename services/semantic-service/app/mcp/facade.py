"""MCP-facing read tools (SEM-FR-080/081).

Exposed as plain REST endpoints under /api/v1/tools/* with JSON-Schema I/O;
the real MCP server wrapper (tool-plane registration, BRD 13) is stubbed TODO.
Every invocation is audited as `ai.tool_invoked.v1`. compile_metric_sql routes
through the SAME CompileService as /compile and /compile/chart — the SEM-FR-081
byte-identity guarantee is structural, and a contract test asserts it (AC-5).
"""

from __future__ import annotations

from app.domain.definition import parse_definition
from app.domain.errors import ModelNotPublished, NotFound, UnknownMetric
from app.domain.services import (
    CallCtx,
    CompileService,
    ModelService,
    ServiceDeps,
    VerifiedQueryService,
    _Base,
)
from app.domain.urn import tool_urn
from app.events.envelope import make_envelope

TOOL_SCHEMAS: dict[str, dict] = {
    "get_metrics": {
        "description": "Governed measures of the workspace's published semantic models",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "model": {"type": "string",
                          "description": "model name or id; omit for all models"},
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}, "description": {"type": ["string", "null"]},
                        "agg": {"type": ["string", "null"]}, "entity": {"type": ["string", "null"]},
                        "synonyms": {"type": "array", "items": {"type": "string"}},
                        "deprecated": {"type": "boolean"},
                        "successor": {"type": ["string", "null"]},
                        "model": {"type": "string"}, "model_version": {"type": "string"},
                    },
                }},
            },
        },
    },
    "get_dimensions": {
        "description": "Governed dimensions, optionally scoped to a metric's model",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "model": {"type": "string"},
                "metric": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "dimensions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}, "type": {"type": "string"},
                        "entity": {"type": "string"},
                        "time_grains": {"type": "array", "items": {"type": "string"}},
                        "sample_values": {"type": "array"},
                        "model": {"type": "string"}, "model_version": {"type": "string"},
                    },
                }},
            },
        },
    },
    "compile_metric_sql": {
        "description": "Compile a metric request to safe parameterized SQL "
                       "(validate=true, agent ceilings)",
        "input_schema": {
            "type": "object",
            "required": ["model", "metrics"],
            "properties": {
                "model": {"type": "string"}, "workspace_id": {"type": "string"},
                "metrics": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "dimensions": {"type": "array"},
                "filters": {"type": "array"},
                "time_range": {"type": "object"},
                "order_by": {"type": "array"},
                "limit": {"type": "integer"},
                "having": {"type": "array"},
                "join_paths": {"type": "array", "items": {"type": "string"}},
                "dialect": {"type": "string",
                            "enum": ["trino", "duckdb", "athena", "bigquery", "synapse"]},
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"}, "params": {"type": "array"},
                "output_schema": {"type": "array"}, "provenance": {"type": "object"},
                "validation": {"type": "object"}, "warnings": {"type": "array"},
            },
        },
    },
    "search_verified_queries": {
        "description": "Semantic search over approved verified NL<->SQL pairs",
        "input_schema": {
            "type": "object",
            "required": ["workspace_id", "q"],
            "properties": {
                "workspace_id": {"type": "string"},
                "q": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"results": {"type": "array"}},
        },
    },
}


class McpFacade(_Base):
    def __init__(self, deps: ServiceDeps, model_service: ModelService,
                 compile_service: CompileService, vq_service: VerifiedQueryService):
        super().__init__(deps)
        self.models = model_service
        self.compiles = compile_service
        self.verified = vq_service

    async def _audit_tool(self, ctx: CallCtx, tool: str, args: dict) -> None:
        async with self.uow(ctx.tenant_id) as uow:
            await uow.outbox.add(
                self.settings.events_topic,
                make_envelope(
                    event_type="ai.tool_invoked.v1",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    via_agent=ctx.via_agent,
                    resource_urn=tool_urn(ctx.tenant_id, tool),
                    payload={"tool": tool, "args": args},
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()

    async def _published_definitions(self, ctx: CallCtx, model_ref: str | None,
                                     workspace_id: str | None) -> list[tuple]:
        """[(model, version, Definition)] for published models in scope."""
        out = []
        async with self.uow(ctx.tenant_id) as uow:
            if model_ref:
                model = await self._resolve_model(ctx, uow, model_ref, workspace_id)
                candidates = [model]
            else:
                candidates = [
                    m for m in await uow.models.all_active()
                    if workspace_id is None or m.workspace_id == workspace_id
                ]
            for model in candidates:
                if not model.published_version_id:
                    if model_ref:
                        raise ModelNotPublished(
                            f"model {model.name!r} has no published version")
                    continue
                version = await uow.versions.get_by_id(model.published_version_id)
                if version is None:
                    continue
                out.append((model, version, parse_definition(version.definition)))
        return out

    async def get_metrics(self, ctx: CallCtx, model: str | None = None,
                          workspace_id: str | None = None) -> dict:
        await self._audit_tool(ctx, "get_metrics",
                               {"model": model, "workspace_id": workspace_id})
        metrics = []
        for m, version, defn in await self._published_definitions(ctx, model,
                                                                  workspace_id):
            label = f"{m.name}@v{version.version_no}"
            for measure in defn.measures.values():
                item = {
                    "name": measure.name, "description": measure.description,
                    "agg": measure.agg, "entity": measure.entity,
                    "synonyms": measure.synonyms, "deprecated": measure.deprecated,
                    "model": m.name, "model_version": label,
                }
                if measure.deprecated and measure.successor:
                    item["successor"] = measure.successor
                metrics.append(item)
        return {"metrics": metrics}

    async def get_dimensions(self, ctx: CallCtx, model: str | None = None,
                             metric: str | None = None,
                             workspace_id: str | None = None) -> dict:
        await self._audit_tool(ctx, "get_dimensions",
                               {"model": model, "metric": metric,
                                "workspace_id": workspace_id})
        scoped = await self._published_definitions(ctx, model, workspace_id)
        if metric:
            scoped = [(m, v, d) for m, v, d in scoped if metric in d.measures]
            if not scoped:
                raise UnknownMetric(f"unknown metric {metric!r}")
        dimensions = []
        for m, version, defn in scoped:
            label = f"{m.name}@v{version.version_no}"
            for dim in defn.dimensions.values():
                item = {
                    "name": dim.name, "type": dim.dim_type, "entity": dim.entity,
                    "time_grains": dim.time_grains, "synonyms": dim.synonyms,
                    "deprecated": dim.deprecated, "model": m.name,
                    "model_version": label,
                }
                # sample_values from dataset profile top_values (SEM-FR-080)
                entity = defn.entities.get(dim.entity)
                if entity and dim.column:
                    info = await self.deps.dataset_client.get_dataset(
                        ctx.tenant_id, entity.dataset_urn)
                    top = ((info or {}).get("top_values") or {}).get(dim.column)
                    if top:
                        item["sample_values"] = top[:10]
                dimensions.append(item)
        return {"dimensions": dimensions}

    async def compile_metric_sql(self, ctx: CallCtx, request: dict,
                                 token: str | None = None) -> dict:
        await self._audit_tool(ctx, "compile_metric_sql", request)
        return await self.compiles.compile(
            ctx, request, caller_class="agent_tool", validate=True,
            limit_ceiling=self.settings.agent_limit_cap, token=token)

    async def search_verified_queries(self, ctx: CallCtx, workspace_id: str,
                                      q: str, top_k: int = 5) -> dict:
        await self._audit_tool(ctx, "search_verified_queries",
                               {"workspace_id": workspace_id, "q": q, "top_k": top_k})
        results = await self.verified.search(ctx, workspace_id, q, top_k)
        return {"results": results}

    async def invoke(self, ctx: CallCtx, tool: str, args: dict) -> dict:
        if tool == "get_metrics":
            return await self.get_metrics(ctx, args.get("model"),
                                          args.get("workspace_id"))
        if tool == "get_dimensions":
            return await self.get_dimensions(ctx, args.get("model"),
                                             args.get("metric"),
                                             args.get("workspace_id"))
        if tool == "compile_metric_sql":
            return await self.compile_metric_sql(ctx, args)
        if tool == "search_verified_queries":
            return await self.search_verified_queries(
                ctx, args.get("workspace_id") or "", args.get("q") or "",
                int(args.get("top_k") or 5))
        raise NotFound(f"unknown tool {tool!r}")


class McpServer:
    """TODO(prod): real MCP server wrapper — registers TOOL_SCHEMAS in the
    tool-registry with version + deprecation window (BRD 13) and serves the MCP
    protocol via the gateway. The REST facade above is the callable surface."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "TODO: MCP server wrapper — REST facade at /api/v1/tools/* in dev")

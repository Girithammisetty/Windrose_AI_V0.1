"""MCP facade (EXP-FR-052).

Agents access experiment-service exclusively through these governed tools. Read
tools (`experiment.runs.search`, `experiment.runs.compare`, `experiment.models.get`,
`experiment.model_card.get`) apply the same filters + RLS as the REST API. The
write tools (`experiment.model.register`, `experiment.model.promote`) are the
`write-proposal` tier — a promotion to `production` can never auto-execute; it
creates a pending promotion whose human approval IS the proposal decision
(EXP-FR-034, MASTER-FR-041 dual attribution).
"""

from __future__ import annotations

from app.domain.services import (
    CallCtx,
    CardService,
    CompareService,
    PromotionService,
    QueryService,
    RegistryService,
)


class McpFacade:
    def __init__(self, query: QueryService, compare: CompareService,
                 registry: RegistryService, promotion: PromotionService, card: CardService):
        self.query = query
        self.compare = compare
        self.registry = registry
        self.promotion = promotion
        self.card = card

    async def runs_search(self, ctx: CallCtx, *, experiment_id=None, status=None,
                          algorithm=None, tag=None, metric_predicates=None,
                          param_predicates=None, sort="-created_at", limit=50):
        experiment_ids = [experiment_id] if experiment_id else None
        page = await self.query.search_runs(
            ctx, experiment_ids=experiment_ids, status=status, algorithm=algorithm, tag=tag,
            metric_predicates=metric_predicates or [], param_predicates=param_predicates or [],
            sort=sort, limit=limit, cursor=None)
        from app.domain.services import _run_payload

        return [_run_payload(ctx, r) for r in page.items]

    async def runs_compare(self, ctx: CallCtx, run_ids, metrics=None, params=None):
        return await self.compare.compare(ctx, run_ids=run_ids, metrics=metrics, params=params,
                                          include_all=metrics is None, cursor=None)

    async def models_list(self, ctx: CallCtx, *, workspace_id: str | None = None,
                          stage: str | None = None, limit: int = 50):
        """`experiment.models.list` (read tier): the registered-model catalog,
        RLS-scoped to the tenant, optionally filtered by workspace / lifecycle
        stage. Grounds agents (e.g. the batch-inference agent) that must resolve a
        registered model by name before reading its versions via `models.get`."""
        page = await self.registry.list_models(ctx, workspace_id, stage, limit, None)
        return page.items

    async def models_get(self, ctx: CallCtx, model_id: str):
        return await self.registry.get_model(ctx, model_id)

    async def model_card_get(self, ctx: CallCtx, model_id: str, version: int):
        return await self.card.get_card(ctx, model_id, version)

    async def model_register(self, ctx: CallCtx, experiment_id: str, run_id: str, payload: dict):
        """write-proposal tier: creates a version at stage none (no auto-promote)."""
        return await self.registry.register(ctx, experiment_id, run_id, payload)

    async def model_promote(self, ctx: CallCtx, model_id: str, version: int, payload: dict):
        """write-proposal tier: creates a PENDING promotion (never auto-executes,
        least of all to production). Human approval is the proposal decision."""
        return await self.promotion.promote(ctx, model_id, version, payload)

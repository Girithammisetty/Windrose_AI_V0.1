"""SQL repositories + unit of work (RLS-bound per MASTER-FR-001).

Every tenant UoW opens a transaction and sets ``app.tenant_id`` so Postgres RLS
applies to the non-privileged application role. Worker sessions
(``app.worker=true``) drive the outbox relay and the cross-tenant promotion-
expiry sweep.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.entities import (
    STAGE,
    Experiment,
    ModelCard,
    ModelVersion,
    Promotion,
    RegisteredModel,
    Run,
    RunArtifact,
    RunMetric,
    RunParam,
    RunTag,
)
from app.store.orm import (
    ExperimentRow,
    IdempotencyKeyRow,
    MirrorInboxRow,
    ModelCardRow,
    ModelRegistrationLogRow,
    ModelVersionRow,
    OutboxRow,
    ProcessedEventRow,
    PromotionRow,
    ReconciliationWatermarkRow,
    RegisteredModelRow,
    RunArtifactRow,
    RunMetricHistoryRow,
    RunMetricRow,
    RunNoteRow,
    RunParamRow,
    RunRow,
    RunTagRow,
)
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7

_EXPERIMENT_FIELDS = [f.name for f in dataclasses.fields(Experiment)]
_RUN_FIELDS = [f.name for f in dataclasses.fields(Run)]
_MODEL_FIELDS = [f.name for f in dataclasses.fields(RegisteredModel)]
_VERSION_FIELDS = [f.name for f in dataclasses.fields(ModelVersion)]
_PROMOTION_FIELDS = [f.name for f in dataclasses.fields(Promotion)]
_CARD_FIELDS = [f.name for f in dataclasses.fields(ModelCard)]


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _to_entity(row, fields, cls):
    return cls(**{f: getattr(row, f) for f in fields})


def _apply(row, entity, fields):
    for f in fields:
        setattr(row, f, getattr(entity, f))


@dataclasses.dataclass(slots=True)
class Page:
    items: list
    next_cursor: str | None
    has_more: bool


def _offset(cursor: str | None) -> int:
    return int(decode_cursor(cursor).get("o", 0)) if cursor else 0


# --- experiments ------------------------------------------------------------


class SqlExperimentRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, exp: Experiment) -> None:
        row = ExperimentRow()
        _apply(row, exp, _EXPERIMENT_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, exp_id: str, include_deleted: bool = False) -> Experiment | None:
        row = await self.s.get(ExperimentRow, exp_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _EXPERIMENT_FIELDS, Experiment)

    async def get_by_name(self, workspace_id: str, name: str) -> Experiment | None:
        stmt = select(ExperimentRow).where(
            ExperimentRow.workspace_id == workspace_id,
            func.lower(ExperimentRow.name) == name.lower(),
            ExperimentRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _EXPERIMENT_FIELDS, Experiment) if row else None

    async def get_by_mlflow_id(self, mlflow_experiment_id: str) -> Experiment | None:
        stmt = select(ExperimentRow).where(
            ExperimentRow.mlflow_experiment_id == mlflow_experiment_id
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _EXPERIMENT_FIELDS, Experiment) if row else None

    async def update(self, exp: Experiment) -> None:
        row = await self.s.get(ExperimentRow, exp.id)
        if row is not None:
            _apply(row, exp, _EXPERIMENT_FIELDS)
            await self.s.flush()

    async def list(self, workspace_id: str | None, archived: bool, limit: int,
                   cursor: str | None) -> Page:
        stmt = select(ExperimentRow)
        stmt = stmt.where(
            ExperimentRow.deleted_at.isnot(None) if archived
            else ExperimentRow.deleted_at.is_(None)
        )
        if workspace_id:
            stmt = stmt.where(ExperimentRow.workspace_id == workspace_id)
        stmt = stmt.order_by(ExperimentRow.created_at.desc(), ExperimentRow.id.desc())
        offset = _offset(cursor)
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[_to_entity(r, _EXPERIMENT_FIELDS, Experiment) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def all_active(self) -> list[Experiment]:
        rows = (
            await self.s.execute(select(ExperimentRow).where(ExperimentRow.deleted_at.is_(None)))
        ).scalars().all()
        return [_to_entity(r, _EXPERIMENT_FIELDS, Experiment) for r in rows]


# --- runs + mirror child tables ---------------------------------------------


class SqlRunRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, run: Run) -> None:
        row = RunRow()
        _apply(row, run, _RUN_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, run_id: str, include_deleted: bool = False) -> Run | None:
        row = await self.s.get(RunRow, run_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _RUN_FIELDS, Run)

    async def get_by_mlflow_run_id(self, mlflow_run_id: str,
                                   include_deleted: bool = True) -> Run | None:
        stmt = select(RunRow).where(RunRow.mlflow_run_id == mlflow_run_id)
        if not include_deleted:
            stmt = stmt.where(RunRow.deleted_at.is_(None))
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _RUN_FIELDS, Run) if row else None

    async def update(self, run: Run) -> None:
        row = await self.s.get(RunRow, run.id)
        if row is not None:
            _apply(row, run, _RUN_FIELDS)
            await self.s.flush()

    async def get_many(self, run_ids: list[str]) -> list[Run]:
        rows = (
            await self.s.execute(
                select(RunRow).where(RunRow.id.in_(run_ids), RunRow.deleted_at.is_(None))
            )
        ).scalars().all()
        return [_to_entity(r, _RUN_FIELDS, Run) for r in rows]

    async def list_by_experiment(self, experiment_id: str, limit: int,
                                 cursor: str | None) -> Page:
        stmt = (
            select(RunRow)
            .where(RunRow.experiment_id == experiment_id, RunRow.deleted_at.is_(None))
            .order_by(RunRow.created_at.desc(), RunRow.id.desc())
        )
        offset = _offset(cursor)
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[_to_entity(r, _RUN_FIELDS, Run) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def search(self, *, experiment_ids: list[str] | None, status: int | None,
                     algorithm: str | None, tag: tuple[str, str] | None,
                     metric_predicates: list[tuple[str, str, float]],
                     param_predicates: list[tuple[str, str]],
                     sort: str, limit: int, cursor: str | None) -> Page:
        stmt = select(RunRow).where(RunRow.deleted_at.is_(None))
        if experiment_ids:
            stmt = stmt.where(RunRow.experiment_id.in_(experiment_ids))
        if status is not None:
            stmt = stmt.where(RunRow.status == status)
        if algorithm:
            stmt = stmt.where(RunRow.algorithm == algorithm)
        if tag:
            tag_alias = RunTagRow.__table__.alias("t_filter")
            stmt = stmt.where(
                select(1).select_from(tag_alias).where(
                    tag_alias.c.run_id == RunRow.id,
                    tag_alias.c.key == tag[0],
                    tag_alias.c.value == tag[1],
                ).exists()
            )
        for key, op, value in metric_predicates:
            m = RunMetricRow.__table__.alias(f"m_{abs(hash((key, op))) % 10000}")
            cmp = {
                "gte": m.c.value >= value, "lte": m.c.value <= value,
                "gt": m.c.value > value, "lt": m.c.value < value, "eq": m.c.value == value,
            }[op]
            stmt = stmt.where(
                select(1).select_from(m).where(
                    m.c.run_id == RunRow.id, m.c.key == key, cmp
                ).exists()
            )
        for key, value in param_predicates:
            p = RunParamRow.__table__.alias(f"p_{abs(hash(key)) % 10000}")
            stmt = stmt.where(
                select(1).select_from(p).where(
                    p.c.run_id == RunRow.id, p.c.key == key, p.c.value == value
                ).exists()
            )

        sort_metric = None
        if sort.lstrip("-").startswith("metric."):
            sort_metric = sort.lstrip("-").split(".", 1)[1]
        offset = _offset(cursor)
        if sort_metric:
            ms = RunMetricRow.__table__.alias("m_sort")
            stmt = stmt.join(
                ms, and_(ms.c.run_id == RunRow.id, ms.c.key == sort_metric)
            ).order_by(ms.c.value.desc() if sort.startswith("-") else ms.c.value.asc())
        else:
            col = RunRow.created_at
            stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc(),
                                 RunRow.id.desc())
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[_to_entity(r, _RUN_FIELDS, Run) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def best(self, experiment_id: str, metric: str, direction: str,
                   status: int | None) -> Run | None:
        m = RunMetricRow.__table__.alias("m_best")
        stmt = (
            select(RunRow)
            .join(m, and_(m.c.run_id == RunRow.id, m.c.key == metric))
            .where(RunRow.experiment_id == experiment_id, RunRow.deleted_at.is_(None))
        )
        if status is not None:
            stmt = stmt.where(RunRow.status == status)
        stmt = stmt.order_by(m.c.value.desc() if direction == "max" else m.c.value.asc()).limit(1)
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _RUN_FIELDS, Run) if row else None

    # -- params
    async def upsert_param(self, param: RunParam) -> bool:
        """Write-once param (EXP-FR-012/BR-1): a changed value flags param_conflict
        and is NOT overwritten. Returns True when a conflict was detected."""
        existing = await self.s.get(RunParamRow, (param.run_id, param.key))
        if existing is None:
            self.s.add(RunParamRow(
                run_id=param.run_id, key=param.key, tenant_id=param.tenant_id,
                value=param.value, is_hidden=param.is_hidden, param_conflict=False,
            ))
            await self.s.flush()
            return False
        if existing.value != param.value:
            existing.param_conflict = True
            await self.s.flush()
            return True
        return False

    async def get_params(self, run_id: str) -> list[RunParam]:
        rows = (
            await self.s.execute(select(RunParamRow).where(RunParamRow.run_id == run_id))
        ).scalars().all()
        return [RunParam(r.run_id, r.tenant_id, r.key, r.value, r.is_hidden, r.param_conflict)
                for r in rows]

    async def params_for_runs(self, run_ids: list[str]) -> dict[str, dict[str, str]]:
        rows = (
            await self.s.execute(select(RunParamRow).where(RunParamRow.run_id.in_(run_ids)))
        ).scalars().all()
        out: dict[str, dict[str, str]] = {}
        for r in rows:
            out.setdefault(r.key, {})[r.run_id] = r.value
        return out

    # -- metrics
    async def upsert_metric(self, metric: RunMetric) -> None:
        stmt = pg_insert(RunMetricRow).values(
            run_id=metric.run_id, key=metric.key, tenant_id=metric.tenant_id,
            value=metric.value, step=metric.step, logged_at=metric.logged_at,
        ).on_conflict_do_update(
            index_elements=["run_id", "key"],
            set_={"value": metric.value, "step": metric.step, "logged_at": metric.logged_at},
            where=RunMetricRow.logged_at <= metric.logged_at,
        )
        await self.s.execute(stmt)

    async def append_metric_history(self, metric: RunMetric) -> None:
        self.s.add(RunMetricHistoryRow(
            id=str(uuid7()), tenant_id=metric.tenant_id, run_id=metric.run_id,
            key=metric.key, step=metric.step, value=metric.value, logged_at=metric.logged_at,
        ))
        await self.s.flush()

    async def get_metrics(self, run_id: str) -> list[RunMetric]:
        rows = (
            await self.s.execute(select(RunMetricRow).where(RunMetricRow.run_id == run_id))
        ).scalars().all()
        return [RunMetric(r.run_id, r.tenant_id, r.key, r.value, r.step, r.logged_at)
                for r in rows]

    async def metrics_for_runs(self, run_ids: list[str]) -> dict[str, dict[str, float]]:
        rows = (
            await self.s.execute(select(RunMetricRow).where(RunMetricRow.run_id.in_(run_ids)))
        ).scalars().all()
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            out.setdefault(r.key, {})[r.run_id] = r.value
        return out

    async def metric_history(self, run_id: str, keys: list[str] | None, limit: int,
                             cursor: str | None) -> Page:
        stmt = select(RunMetricHistoryRow).where(RunMetricHistoryRow.run_id == run_id)
        if keys:
            stmt = stmt.where(RunMetricHistoryRow.key.in_(keys))
        stmt = stmt.order_by(
            RunMetricHistoryRow.key.asc(), RunMetricHistoryRow.step.asc(),
            RunMetricHistoryRow.id.asc(),
        )
        offset = _offset(cursor)
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        items = [
            {"key": r.key, "step": r.step, "value": r.value,
             "logged_at": r.logged_at.isoformat()}
            for r in rows[:limit]
        ]
        return Page(items=items,
                    next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
                    has_more=has_more)

    # -- tags
    async def upsert_tag(self, tag: RunTag) -> None:
        stmt = pg_insert(RunTagRow).values(
            run_id=tag.run_id, key=tag.key, tenant_id=tag.tenant_id, value=tag.value,
        ).on_conflict_do_update(index_elements=["run_id", "key"], set_={"value": tag.value})
        await self.s.execute(stmt)

    async def get_tags(self, run_id: str) -> list[RunTag]:
        rows = (
            await self.s.execute(select(RunTagRow).where(RunTagRow.run_id == run_id))
        ).scalars().all()
        return [RunTag(r.run_id, r.tenant_id, r.key, r.value) for r in rows]

    # -- artifacts
    async def upsert_artifact(self, art: RunArtifact) -> None:
        stmt = pg_insert(RunArtifactRow).values(
            run_id=art.run_id, path=art.path, tenant_id=art.tenant_id,
            size_bytes=art.size_bytes, content_type=art.content_type,
        ).on_conflict_do_update(
            index_elements=["run_id", "path"],
            set_={"size_bytes": art.size_bytes, "content_type": art.content_type},
        )
        await self.s.execute(stmt)

    async def get_artifacts(self, run_id: str) -> list[RunArtifact]:
        rows = (
            await self.s.execute(select(RunArtifactRow).where(RunArtifactRow.run_id == run_id))
        ).scalars().all()
        return [RunArtifact(r.run_id, r.tenant_id, r.path, r.size_bytes, r.content_type)
                for r in rows]

    # -- notes
    async def set_note(self, run_id: str, tenant_id: str, description: str) -> None:
        stmt = pg_insert(RunNoteRow).values(
            run_id=run_id, tenant_id=tenant_id, description=description, updated_at=utcnow(),
        ).on_conflict_do_update(
            index_elements=["run_id"],
            set_={"description": description, "updated_at": utcnow()},
        )
        await self.s.execute(stmt)

    async def get_note(self, run_id: str) -> str | None:
        row = await self.s.get(RunNoteRow, run_id)
        return row.description if row else None

    async def delete_note(self, run_id: str) -> None:
        row = await self.s.get(RunNoteRow, run_id)
        if row is not None:
            await self.s.delete(row)
            await self.s.flush()


# --- registry (models, versions, promotions, log, cards) --------------------


class SqlModelRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add_model(self, model: RegisteredModel) -> None:
        row = RegisteredModelRow()
        _apply(row, model, _MODEL_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get_model(self, model_id: str, include_deleted: bool = False):
        row = await self.s.get(RegisteredModelRow, model_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _MODEL_FIELDS, RegisteredModel)

    async def get_model_by_name(self, workspace_id: str, name: str):
        stmt = select(RegisteredModelRow).where(
            RegisteredModelRow.workspace_id == workspace_id,
            func.lower(RegisteredModelRow.name) == name.lower(),
            RegisteredModelRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _MODEL_FIELDS, RegisteredModel) if row else None

    async def update_model(self, model: RegisteredModel) -> None:
        row = await self.s.get(RegisteredModelRow, model.id)
        if row is not None:
            _apply(row, model, _MODEL_FIELDS)
            await self.s.flush()

    async def lock_model(self, model_id: str) -> None:
        """Per-model transactional mutex (BR-4): serializes concurrent production
        promotions so the second waits and re-validates instead of racing the
        single-production unique index into a 500."""
        await self.s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:mid, 11))"),
            {"mid": model_id},
        )

    async def list_models(self, workspace_id: str | None, stage: int | None,
                          limit: int, cursor: str | None,
                          ids: list[str] | None = None) -> Page:
        stmt = select(RegisteredModelRow).where(RegisteredModelRow.deleted_at.is_(None))
        if ids is not None:
            stmt = stmt.where(RegisteredModelRow.id.in_(ids))
        if workspace_id:
            stmt = stmt.where(RegisteredModelRow.workspace_id == workspace_id)
        if stage is not None:
            v = ModelVersionRow.__table__.alias("v_stage")
            stmt = stmt.where(
                select(1).select_from(v).where(
                    v.c.model_id == RegisteredModelRow.id, v.c.stage == stage,
                    v.c.deleted_at.is_(None),
                ).exists()
            )
        stmt = stmt.order_by(RegisteredModelRow.created_at.desc(), RegisteredModelRow.id.desc())
        offset = _offset(cursor)
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[_to_entity(r, _MODEL_FIELDS, RegisteredModel) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def add_version(self, version: ModelVersion) -> None:
        row = ModelVersionRow()
        _apply(row, version, _VERSION_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get_version(self, model_id: str, version: int) -> ModelVersion | None:
        stmt = select(ModelVersionRow).where(
            ModelVersionRow.model_id == model_id, ModelVersionRow.version == version
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def get_version_by_id(self, version_id: str) -> ModelVersion | None:
        row = await self.s.get(ModelVersionRow, version_id)
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def update_version(self, version: ModelVersion) -> None:
        row = await self.s.get(ModelVersionRow, version.id)
        if row is not None:
            _apply(row, version, _VERSION_FIELDS)
            await self.s.flush()

    async def next_version_no(self, model_id: str) -> int:
        await self.s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:mid, 7))"), {"mid": model_id}
        )
        result = await self.s.execute(
            select(func.coalesce(func.max(ModelVersionRow.version), 0)).where(
                ModelVersionRow.model_id == model_id
            )
        )
        return int(result.scalar_one()) + 1

    async def production_version(self, model_id: str) -> ModelVersion | None:
        stmt = select(ModelVersionRow).where(
            ModelVersionRow.model_id == model_id, ModelVersionRow.stage == STAGE["production"],
            ModelVersionRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def list_versions(self, model_id: str) -> list[ModelVersion]:
        rows = (
            await self.s.execute(
                select(ModelVersionRow).where(ModelVersionRow.model_id == model_id)
                .order_by(ModelVersionRow.version.asc())
            )
        ).scalars().all()
        return [_to_entity(r, _VERSION_FIELDS, ModelVersion) for r in rows]

    # -- promotions
    async def add_promotion(self, promotion: Promotion) -> None:
        row = PromotionRow()
        _apply(row, promotion, _PROMOTION_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get_promotion(self, promotion_id: str) -> Promotion | None:
        row = await self.s.get(PromotionRow, promotion_id)
        return _to_entity(row, _PROMOTION_FIELDS, Promotion) if row else None

    async def update_promotion(self, promotion: Promotion) -> None:
        row = await self.s.get(PromotionRow, promotion.id)
        if row is not None:
            _apply(row, promotion, _PROMOTION_FIELDS)
            await self.s.flush()

    async def pending_for_version(self, model_version_id: str) -> Promotion | None:
        stmt = select(PromotionRow).where(
            PromotionRow.model_version_id == model_version_id,
            PromotionRow.status == 0,
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _PROMOTION_FIELDS, Promotion) if row else None

    async def list_promotions(self, model_version_id: str, limit: int,
                              cursor: str | None) -> Page:
        stmt = (
            select(PromotionRow)
            .where(PromotionRow.model_version_id == model_version_id)
            .order_by(PromotionRow.created_at.desc(), PromotionRow.id.desc())
        )
        offset = _offset(cursor)
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[_to_entity(r, _PROMOTION_FIELDS, Promotion) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def pending_expired_before(self, cutoff: datetime) -> list[Promotion]:
        """Worker-session query (cross-tenant) for the expiry sweep."""
        stmt = select(PromotionRow).where(
            PromotionRow.status == 0, PromotionRow.expires_at <= cutoff
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _PROMOTION_FIELDS, Promotion) for r in rows]

    # -- registration log
    async def add_registration_log(self, *, model_version_id: str, experiment_id: str,
                                   tenant_id: str, run_snapshot: dict, registered_by: str,
                                   via_agent: dict | None) -> None:
        self.s.add(ModelRegistrationLogRow(
            id=str(uuid7()), tenant_id=tenant_id, model_version_id=model_version_id,
            experiment_id=experiment_id, run_snapshot=run_snapshot,
            registered_by=registered_by, via_agent=via_agent, created_at=utcnow(),
        ))
        await self.s.flush()

    # -- cards
    async def upsert_card(self, card: ModelCard) -> None:
        row = await self.s.get(ModelCardRow, card.model_version_id)
        if row is None:
            new = ModelCardRow()
            _apply(new, card, _CARD_FIELDS)
            self.s.add(new)
        else:
            _apply(row, card, _CARD_FIELDS)
        await self.s.flush()

    async def get_card(self, model_version_id: str) -> ModelCard | None:
        row = await self.s.get(ModelCardRow, model_version_id)
        return _to_entity(row, _CARD_FIELDS, ModelCard) if row else None

    async def cards_referencing_dataset(self, dataset_urn: str) -> list[ModelCard]:
        """Cards whose auto_fields.input_dataset_urns contains ``dataset_urn``
        (EXP-FR-040 / §6 dataset.deleted flagging)."""
        stmt = select(ModelCardRow).where(
            text("auto_fields->'input_dataset_urns' @> :urn::jsonb").bindparams(
                urn=f'["{dataset_urn}"]')
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _CARD_FIELDS, ModelCard) for r in rows]


# --- mirror inbox + watermarks ----------------------------------------------


class SqlInboxRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, *, delivery_id: str, tenant_id: str, event_type: str,
                  payload: dict) -> bool:
        stmt = pg_insert(MirrorInboxRow).values(
            delivery_id=delivery_id, tenant_id=tenant_id, event_type=event_type,
            payload=payload, received_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["delivery_id"]).returning(
            MirrorInboxRow.delivery_id
        )
        inserted = (await self.s.execute(stmt)).scalar()
        return inserted is not None

    async def unapplied(self, limit: int = 100) -> list[MirrorInboxRow]:
        stmt = (
            select(MirrorInboxRow).where(MirrorInboxRow.applied_at.is_(None))
            .order_by(MirrorInboxRow.received_at.asc()).limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars().all())

    async def mark_applied(self, delivery_id: str) -> None:
        await self.s.execute(
            update(MirrorInboxRow).where(MirrorInboxRow.delivery_id == delivery_id)
            .values(applied_at=utcnow(), error=None)
        )

    async def mark_error(self, delivery_id: str, error: str) -> None:
        await self.s.execute(
            update(MirrorInboxRow).where(MirrorInboxRow.delivery_id == delivery_id)
            .values(error=error[:500])
        )


class SqlWatermarkRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, mlflow_experiment_id: str, tenant_id: str) -> datetime | None:
        row = await self.s.get(ReconciliationWatermarkRow, (tenant_id, mlflow_experiment_id))
        return row.last_reconciled_at if row else None

    async def upsert(self, mlflow_experiment_id: str, tenant_id: str, ts: datetime) -> None:
        stmt = pg_insert(ReconciliationWatermarkRow).values(
            tenant_id=tenant_id, mlflow_experiment_id=mlflow_experiment_id,
            last_reconciled_at=ts,
        ).on_conflict_do_update(
            index_elements=["tenant_id", "mlflow_experiment_id"],
            set_={"last_reconciled_at": ts},
        )
        await self.s.execute(stmt)


class SqlOutboxRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def add(self, topic: str, envelope: dict) -> None:
        self.s.add(OutboxRow(
            id=str(uuid7()), tenant_id=self.tenant_id, topic=topic,
            event_type=envelope["event_type"], payload=envelope, created_at=utcnow(),
        ))
        await self.s.flush()


class SqlIdempotencyRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def get(self, key: str) -> dict | None:
        row = await self.s.get(IdempotencyKeyRow, (self.tenant_id, key))
        if row is None:
            return None
        return {"request_hash": row.request_hash, "status_code": row.status_code,
                "body": row.response_body}

    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None:
        self.s.add(IdempotencyKeyRow(
            tenant_id=self.tenant_id, key=key, request_hash=request_hash,
            status_code=status_code, response_body=body, created_at=utcnow(),
        ))
        await self.s.flush()


# --- unit of work -----------------------------------------------------------


class SqlUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker, tenant_id: str,
                 *, worker: bool = False):
        self.tenant_id = tenant_id
        self._session_factory = session_factory
        self._worker = worker
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlUnitOfWork:
        self._session = self._session_factory()
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": self.tenant_id}
        )
        if self._worker:
            await self._session.execute(text("SELECT set_config('app.worker', 'true', true)"))
        self.experiments = SqlExperimentRepo(self._session)
        self.runs = SqlRunRepo(self._session)
        self.models = SqlModelRepo(self._session)
        self.inbox = SqlInboxRepo(self._session)
        self.watermarks = SqlWatermarkRepo(self._session)
        self.outbox = SqlOutboxRepo(self._session, self.tenant_id)
        self.idempotency = SqlIdempotencyRepo(self._session, self.tenant_id)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                await self.commit()
            else:
                await self.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": self.tenant_id}
        )
        if self._worker:
            await self._session.execute(text("SELECT set_config('app.worker', 'true', true)"))

    async def rollback(self) -> None:
        await self._session.rollback()


def sql_uow_factory(session_factory: async_sessionmaker):
    def factory(tenant_id: str, *, worker: bool = False) -> SqlUnitOfWork:
        return SqlUnitOfWork(session_factory, tenant_id, worker=worker)

    return factory


class SqlDedupStore:
    """Durable consumer dedup on processed_events (handle-then-mark)."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            return await session.get(ProcessedEventRow, event_id) is not None

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            await session.execute(
                pg_insert(ProcessedEventRow)
                .values(event_id=event_id, tenant_id=tenant_id, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=["event_id"])
            )
            await session.commit()


class OutboxDispatcher:
    """Polls unpublished outbox rows and publishes to the bus (MASTER-FR-034)."""

    def __init__(self, session_factory: async_sessionmaker, bus, batch_size: int = 100):
        self._session_factory = session_factory
        self._bus = bus
        self._batch = batch_size

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            await session.execute(text("SELECT set_config('app.worker', 'true', true)"))
            stmt = (
                select(OutboxRow).where(OutboxRow.published_at.is_(None))
                .order_by(OutboxRow.created_at.asc()).limit(self._batch)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                await self._bus.publish(row.topic, row.payload)
            if rows:
                await session.execute(
                    update(OutboxRow).where(OutboxRow.id.in_([r.id for r in rows]))
                    .values(published_at=utcnow())
                )
            await session.commit()
            return len(rows)

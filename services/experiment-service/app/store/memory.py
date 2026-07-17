"""In-memory unit-of-work (unit/dev tier ONLY — never wired from app.main).

Mirrors the SQL repo surface with tenant-scoped dict storage so cross-tenant
reads return None (the in-memory analogue of RLS for the unit isolation suite).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime

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
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7


@dataclass
class MemoryState:
    experiments: dict[str, Experiment] = field(default_factory=dict)
    runs: dict[str, Run] = field(default_factory=dict)
    params: dict[tuple[str, str], RunParam] = field(default_factory=dict)
    metrics: dict[tuple[str, str], RunMetric] = field(default_factory=dict)
    metric_history: list[dict] = field(default_factory=list)
    tags: dict[tuple[str, str], RunTag] = field(default_factory=dict)
    artifacts: dict[tuple[str, str], RunArtifact] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    models: dict[str, RegisteredModel] = field(default_factory=dict)
    versions: dict[str, ModelVersion] = field(default_factory=dict)
    promotions: dict[str, Promotion] = field(default_factory=dict)
    registration_log: list[dict] = field(default_factory=list)
    cards: dict[str, ModelCard] = field(default_factory=dict)
    inbox: dict[str, dict] = field(default_factory=dict)
    watermarks: dict[tuple[str, str], datetime] = field(default_factory=dict)
    outbox: list[dict] = field(default_factory=list)
    idempotency: dict[tuple[str, str], dict] = field(default_factory=dict)


def _offset(cursor: str | None) -> int:
    return int(decode_cursor(cursor).get("o", 0)) if cursor else 0


def _page(items: list, limit: int, cursor: str | None):
    from app.store.sql import Page

    offset = _offset(cursor)
    window = items[offset : offset + limit]
    has_more = offset + limit < len(items)
    return Page(items=[copy.deepcopy(i) for i in window],
                next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
                has_more=has_more)


class _ExperimentRepo:
    def __init__(self, st: MemoryState, tenant: str):
        self.st, self.t = st, tenant

    async def add(self, exp: Experiment):
        self.st.experiments[exp.id] = copy.deepcopy(exp)

    async def get(self, exp_id, include_deleted=False):
        e = self.st.experiments.get(exp_id)
        if not e or e.tenant_id != self.t or (e.deleted_at and not include_deleted):
            return None
        return copy.deepcopy(e)

    async def get_by_name(self, workspace_id, name):
        for e in self.st.experiments.values():
            if (e.tenant_id == self.t and e.workspace_id == workspace_id
                    and e.name.lower() == name.lower() and not e.deleted_at):
                return copy.deepcopy(e)
        return None

    async def get_by_mlflow_id(self, mlflow_experiment_id):
        for e in self.st.experiments.values():
            if e.tenant_id == self.t and e.mlflow_experiment_id == mlflow_experiment_id:
                return copy.deepcopy(e)
        return None

    async def update(self, exp: Experiment):
        self.st.experiments[exp.id] = copy.deepcopy(exp)

    async def list(self, workspace_id, archived, limit, cursor):
        items = sorted(
            (e for e in self.st.experiments.values()
             if e.tenant_id == self.t and (bool(e.deleted_at) == archived)
             and (not workspace_id or e.workspace_id == workspace_id)),
            key=lambda e: (e.created_at, e.id), reverse=True,
        )
        return _page(list(items), limit, cursor)

    async def all_active(self):
        return [copy.deepcopy(e) for e in self.st.experiments.values()
                if e.tenant_id == self.t and not e.deleted_at]


class _RunRepo:
    def __init__(self, st: MemoryState, tenant: str):
        self.st, self.t = st, tenant

    async def add(self, run: Run):
        self.st.runs[run.id] = copy.deepcopy(run)

    async def get(self, run_id, include_deleted=False):
        r = self.st.runs.get(run_id)
        if not r or r.tenant_id != self.t or (r.deleted_at and not include_deleted):
            return None
        return copy.deepcopy(r)

    async def get_by_mlflow_run_id(self, mlflow_run_id, include_deleted=True):
        for r in self.st.runs.values():
            if (r.tenant_id == self.t and r.mlflow_run_id == mlflow_run_id
                    and (include_deleted or not r.deleted_at)):
                return copy.deepcopy(r)
        return None

    async def update(self, run: Run):
        self.st.runs[run.id] = copy.deepcopy(run)

    async def get_many(self, run_ids):
        return [copy.deepcopy(self.st.runs[r]) for r in run_ids
                if r in self.st.runs and self.st.runs[r].tenant_id == self.t
                and not self.st.runs[r].deleted_at]

    async def list_by_experiment(self, experiment_id, limit, cursor):
        items = sorted(
            (r for r in self.st.runs.values()
             if r.tenant_id == self.t and r.experiment_id == experiment_id and not r.deleted_at),
            key=lambda r: (r.created_at, r.id), reverse=True,
        )
        return _page(list(items), limit, cursor)

    def _matches_metric(self, run_id, key, op, value):
        m = self.st.metrics.get((run_id, key))
        if m is None:
            return False
        return {
            "gte": m.value >= value, "lte": m.value <= value, "gt": m.value > value,
            "lt": m.value < value, "eq": m.value == value,
        }[op]

    async def search(self, *, experiment_ids, status, algorithm, tag, metric_predicates,
                     param_predicates, sort, limit, cursor):
        out = []
        for r in self.st.runs.values():
            if r.tenant_id != self.t or r.deleted_at:
                continue
            if experiment_ids and r.experiment_id not in experiment_ids:
                continue
            if status is not None and r.status != status:
                continue
            if algorithm and r.algorithm != algorithm:
                continue
            if tag and self.st.tags.get((r.id, tag[0])) is None:
                continue
            if tag and self.st.tags[(r.id, tag[0])].value != tag[1]:
                continue
            if any(not self._matches_metric(r.id, k, op, v) for k, op, v in metric_predicates):
                continue
            ok = True
            for k, v in param_predicates:
                p = self.st.params.get((r.id, k))
                if p is None or p.value != v:
                    ok = False
                    break
            if not ok:
                continue
            out.append(r)
        sort_metric = None
        if sort.lstrip("-").startswith("metric."):
            sort_metric = sort.lstrip("-").split(".", 1)[1]
        if sort_metric:
            out = [r for r in out if (r.id, sort_metric) in self.st.metrics]
            out.sort(key=lambda r: self.st.metrics[(r.id, sort_metric)].value,
                     reverse=sort.startswith("-"))
        else:
            out.sort(key=lambda r: (r.created_at, r.id), reverse=sort.startswith("-"))
        return _page(out, limit, cursor)

    async def best(self, experiment_id, metric, direction, status):
        cands = []
        for r in self.st.runs.values():
            if r.tenant_id != self.t or r.deleted_at or r.experiment_id != experiment_id:
                continue
            if status is not None and r.status != status:
                continue
            m = self.st.metrics.get((r.id, metric))
            if m is not None:
                cands.append((r, m.value))
        if not cands:
            return None
        cands.sort(key=lambda t: t[1], reverse=(direction == "max"))
        return copy.deepcopy(cands[0][0])

    async def upsert_param(self, param: RunParam) -> bool:
        existing = self.st.params.get((param.run_id, param.key))
        if existing is None:
            self.st.params[(param.run_id, param.key)] = copy.deepcopy(param)
            return False
        if existing.value != param.value:
            existing.param_conflict = True
            return True
        return False

    async def get_params(self, run_id):
        return [copy.deepcopy(p) for (rid, _), p in self.st.params.items() if rid == run_id]

    async def params_for_runs(self, run_ids):
        out: dict[str, dict[str, str]] = {}
        for (rid, key), p in self.st.params.items():
            if rid in run_ids:
                out.setdefault(key, {})[rid] = p.value
        return out

    async def upsert_metric(self, metric: RunMetric):
        existing = self.st.metrics.get((metric.run_id, metric.key))
        if existing is None or existing.logged_at <= metric.logged_at:
            self.st.metrics[(metric.run_id, metric.key)] = copy.deepcopy(metric)

    async def append_metric_history(self, metric: RunMetric):
        self.st.metric_history.append({
            "run_id": metric.run_id, "key": metric.key, "step": metric.step,
            "value": metric.value, "logged_at": metric.logged_at,
        })

    async def get_metrics(self, run_id):
        return [copy.deepcopy(m) for (rid, _), m in self.st.metrics.items() if rid == run_id]

    async def metrics_for_runs(self, run_ids):
        out: dict[str, dict[str, float]] = {}
        for (rid, key), m in self.st.metrics.items():
            if rid in run_ids:
                out.setdefault(key, {})[rid] = m.value
        return out

    async def metric_history(self, run_id, keys, limit, cursor):
        rows = [h for h in self.st.metric_history
                if h["run_id"] == run_id and (not keys or h["key"] in keys)]
        rows.sort(key=lambda h: (h["key"], h["step"]))
        items = [{"key": h["key"], "step": h["step"], "value": h["value"],
                  "logged_at": h["logged_at"].isoformat()} for h in rows]
        return _page(items, limit, cursor)

    async def upsert_tag(self, tag: RunTag):
        self.st.tags[(tag.run_id, tag.key)] = copy.deepcopy(tag)

    async def get_tags(self, run_id):
        return [copy.deepcopy(t) for (rid, _), t in self.st.tags.items() if rid == run_id]

    async def upsert_artifact(self, art: RunArtifact):
        self.st.artifacts[(art.run_id, art.path)] = copy.deepcopy(art)

    async def get_artifacts(self, run_id):
        return [copy.deepcopy(a) for (rid, _), a in self.st.artifacts.items() if rid == run_id]

    async def set_note(self, run_id, tenant_id, description):
        self.st.notes[run_id] = description

    async def get_note(self, run_id):
        return self.st.notes.get(run_id)

    async def delete_note(self, run_id):
        self.st.notes.pop(run_id, None)


class _ModelRepo:
    def __init__(self, st: MemoryState, tenant: str):
        self.st, self.t = st, tenant

    async def add_model(self, m: RegisteredModel):
        self.st.models[m.id] = copy.deepcopy(m)

    async def get_model(self, model_id, include_deleted=False):
        m = self.st.models.get(model_id)
        if not m or m.tenant_id != self.t or (m.deleted_at and not include_deleted):
            return None
        return copy.deepcopy(m)

    async def get_model_by_name(self, workspace_id, name):
        for m in self.st.models.values():
            if (m.tenant_id == self.t and m.workspace_id == workspace_id
                    and m.name.lower() == name.lower() and not m.deleted_at):
                return copy.deepcopy(m)
        return None

    async def update_model(self, m: RegisteredModel):
        self.st.models[m.id] = copy.deepcopy(m)

    async def lock_model(self, model_id):
        return None  # in-memory unit tier: single-threaded, no lock needed

    async def list_models(self, workspace_id, stage, limit, cursor, ids=None):
        def has_stage(model_id):
            return any(v.model_id == model_id and v.stage == stage and not v.deleted_at
                       for v in self.st.versions.values())
        id_set = set(ids) if ids is not None else None
        items = sorted(
            (m for m in self.st.models.values()
             if m.tenant_id == self.t and not m.deleted_at
             and (id_set is None or m.id in id_set)
             and (not workspace_id or m.workspace_id == workspace_id)
             and (stage is None or has_stage(m.id))),
            key=lambda m: (m.created_at, m.id), reverse=True,
        )
        return _page(list(items), limit, cursor)

    async def add_version(self, v: ModelVersion):
        self.st.versions[v.id] = copy.deepcopy(v)

    async def get_version(self, model_id, version):
        for v in self.st.versions.values():
            if v.tenant_id == self.t and v.model_id == model_id and v.version == version:
                return copy.deepcopy(v)
        return None

    async def get_version_by_id(self, version_id):
        v = self.st.versions.get(version_id)
        if not v or v.tenant_id != self.t:
            return None
        return copy.deepcopy(v)

    async def update_version(self, v: ModelVersion):
        self.st.versions[v.id] = copy.deepcopy(v)

    async def next_version_no(self, model_id):
        return max((v.version for v in self.st.versions.values()
                    if v.model_id == model_id), default=0) + 1

    async def production_version(self, model_id):
        for v in self.st.versions.values():
            if (v.tenant_id == self.t and v.model_id == model_id
                    and v.stage == STAGE["production"] and not v.deleted_at):
                return copy.deepcopy(v)
        return None

    async def list_versions(self, model_id):
        return sorted(
            (copy.deepcopy(v) for v in self.st.versions.values()
             if v.tenant_id == self.t and v.model_id == model_id),
            key=lambda v: v.version,
        )

    async def add_promotion(self, p: Promotion):
        self.st.promotions[p.id] = copy.deepcopy(p)

    async def get_promotion(self, promotion_id):
        p = self.st.promotions.get(promotion_id)
        if not p or p.tenant_id != self.t:
            return None
        return copy.deepcopy(p)

    async def update_promotion(self, p: Promotion):
        self.st.promotions[p.id] = copy.deepcopy(p)

    async def pending_for_version(self, model_version_id):
        for p in self.st.promotions.values():
            if (p.tenant_id == self.t and p.model_version_id == model_version_id
                    and p.status == 0):
                return copy.deepcopy(p)
        return None

    async def list_promotions(self, model_version_id, limit, cursor):
        items = sorted(
            (p for p in self.st.promotions.values()
             if p.tenant_id == self.t and p.model_version_id == model_version_id),
            key=lambda p: (p.created_at, p.id), reverse=True,
        )
        return _page(list(items), limit, cursor)

    async def pending_expired_before(self, cutoff):
        return [copy.deepcopy(p) for p in self.st.promotions.values()
                if p.status == 0 and p.expires_at and p.expires_at <= cutoff]

    async def add_registration_log(self, *, model_version_id, experiment_id, tenant_id,
                                   run_snapshot, registered_by, via_agent):
        self.st.registration_log.append({
            "id": str(uuid7()), "tenant_id": tenant_id, "model_version_id": model_version_id,
            "experiment_id": experiment_id, "run_snapshot": run_snapshot,
            "registered_by": registered_by, "via_agent": via_agent, "created_at": utcnow(),
        })

    async def upsert_card(self, card: ModelCard):
        self.st.cards[card.model_version_id] = copy.deepcopy(card)

    async def get_card(self, model_version_id):
        c = self.st.cards.get(model_version_id)
        if not c or c.tenant_id != self.t:
            return None
        return copy.deepcopy(c)

    async def cards_referencing_dataset(self, dataset_urn):
        return [copy.deepcopy(c) for c in self.st.cards.values()
                if c.tenant_id == self.t
                and dataset_urn in (c.auto_fields.get("input_dataset_urns") or [])]


class _InboxRepo:
    def __init__(self, st: MemoryState, tenant: str):
        self.st, self.t = st, tenant

    async def add(self, *, delivery_id, tenant_id, event_type, payload):
        if delivery_id in self.st.inbox:
            return False
        self.st.inbox[delivery_id] = {
            "delivery_id": delivery_id, "tenant_id": tenant_id, "event_type": event_type,
            "payload": payload, "received_at": utcnow(), "applied_at": None, "error": None,
        }
        return True

    async def unapplied(self, limit=100):
        from types import SimpleNamespace

        rows = [SimpleNamespace(**r) for r in self.st.inbox.values()
                if r["tenant_id"] == self.t and r["applied_at"] is None]
        return rows[:limit]

    async def mark_applied(self, delivery_id):
        if delivery_id in self.st.inbox:
            self.st.inbox[delivery_id]["applied_at"] = utcnow()
            self.st.inbox[delivery_id]["error"] = None

    async def mark_error(self, delivery_id, error):
        if delivery_id in self.st.inbox:
            self.st.inbox[delivery_id]["error"] = error[:500]


class _WatermarkRepo:
    def __init__(self, st: MemoryState, tenant: str):
        self.st, self.t = st, tenant

    async def get(self, mlflow_experiment_id, tenant_id):
        return self.st.watermarks.get((tenant_id, mlflow_experiment_id))

    async def upsert(self, mlflow_experiment_id, tenant_id, ts):
        self.st.watermarks[(tenant_id, mlflow_experiment_id)] = ts


class _OutboxRepo:
    def __init__(self, st: MemoryState, tenant: str, bus):
        self.st, self.t, self.bus = st, tenant, bus
        self.pending: list[tuple[str, dict]] = []

    async def add(self, topic, envelope):
        self.pending.append((topic, envelope))
        self.st.outbox.append({"topic": topic, "payload": envelope})


class _IdempotencyRepo:
    def __init__(self, st: MemoryState, tenant: str):
        self.st, self.t = st, tenant

    async def get(self, key):
        return self.st.idempotency.get((self.t, key))

    async def put(self, key, request_hash, status_code, body):
        self.st.idempotency[(self.t, key)] = {
            "request_hash": request_hash, "status_code": status_code, "body": body,
        }


class MemoryUnitOfWork:
    def __init__(self, state: MemoryState, tenant_id: str, bus, *, worker: bool = False):
        self.state = state
        self.tenant_id = tenant_id
        self._bus = bus

    async def __aenter__(self):
        self.experiments = _ExperimentRepo(self.state, self.tenant_id)
        self.runs = _RunRepo(self.state, self.tenant_id)
        self.models = _ModelRepo(self.state, self.tenant_id)
        self.inbox = _InboxRepo(self.state, self.tenant_id)
        self.watermarks = _WatermarkRepo(self.state, self.tenant_id)
        self.outbox = _OutboxRepo(self.state, self.tenant_id, self._bus)
        self.idempotency = _IdempotencyRepo(self.state, self.tenant_id)
        self._committed = False
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None and not self._committed:
            await self.commit()

    async def commit(self):
        self._committed = True
        # In-process dispatch of buffered outbox events to the in-memory bus so
        # unit-tier consumers/subscribers observe them (mirrors OutboxDispatcher).
        for topic, envelope in self.outbox.pending:
            await self._bus.publish(topic, envelope)
        self.outbox.pending = []

    async def rollback(self):
        self.outbox.pending = []


def memory_uow_factory(state: MemoryState, bus):
    def factory(tenant_id: str, *, worker: bool = False) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(state, tenant_id, bus, worker=worker)

    return factory

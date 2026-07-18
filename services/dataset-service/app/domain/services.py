"""Application services: orchestration of repos, adapters, and the outbox.

Every mutation writes its event to the outbox inside the same unit of work
(MASTER-FR-034); profiler launches happen strictly after commit.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from app.config import Settings
from app.domain import lineage as lineage_ops
from app.domain.entities import (
    Activity,
    Dataset,
    DatasetStatus,
    DatasetVersion,
    EntityMergeCandidate,
    EntityResolutionConfig,
    EntityResolutionRun,
    Lifecycle,
    LineageEdge,
    MergeCandidateStatus,
    Profile,
    ProfileErrorCategory,
    ProfileStatus,
    ResolvedEntity,
    ResolvedEntityMember,
    Visibility,
)
from app.domain.errors import (
    Conflict,
    Gone,
    NotFound,
    RateLimited,
    SnapshotAlreadyRegistered,
    ValidationFailed,
)
from app.domain.naming import RESOLVE_NAMESPACE, safe_relation
from app.domain.ports import (
    Catalog,
    DatasetFilters,
    ObjectStore,
    Page,
    ProfileJobSpec,
    ProfilerRunner,
    SearchIndex,
    UnitOfWork,
    UowFactory,
)
from app.domain.profiling.engine import SUMMARY_MAX_BYTES
from app.domain.retention import RetentionPolicy, select_expirable
from app.domain.schema_diff import compute_schema_diff
from app.domain.similarity import rank_similar
from app.domain.state import transition_dataset, transition_profile
from app.domain.urn import (
    dataset_urn,
    is_valid_urn,
    parse_urn,
    parse_version_urn,
    version_urn,
)
from app.events.envelope import make_envelope
from app.utils import Clock, etag_for, json_size_bytes, sha256_hex, uuid7


@dataclass(slots=True)
class CallCtx:
    tenant_id: str
    actor: dict
    via_agent: dict | None = None
    trace_id: str | None = None


@dataclass(slots=True)
class ServiceDeps:
    settings: Settings
    clock: Clock
    uow_factory: UowFactory
    catalog: Catalog
    object_store: ObjectStore
    search_index: SearchIndex
    runner_provider: Callable[[], ProfilerRunner] = field(default=lambda: None)  # set by wiring


class _Base:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps
        self.settings = deps.settings
        self.clock = deps.clock

    def uow(self, tenant_id: str) -> UnitOfWork:
        return self.deps.uow_factory(tenant_id)

    async def _emit(self, uow: UnitOfWork, ctx: CallCtx, event_type: str,
                    resource_urn: str, payload: dict) -> None:
        await uow.outbox.add(
            self.settings.events_topic,
            make_envelope(
                event_type=event_type,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                via_agent=ctx.via_agent,
                resource_urn=resource_urn,
                payload=payload,
                trace_id=ctx.trace_id,
            ),
        )

    async def _audit_cross_tenant(self, ctx: CallCtx, resource_urn: str, detail: str) -> None:
        """MASTER-FR-003: audit denied cross-tenant access in its own committed uow."""
        async with self.uow(ctx.tenant_id) as uow:
            await self._emit(
                uow, ctx, "security.cross_tenant_denied", resource_urn, {"detail": detail}
            )
            await uow.commit()


def _columns_from_schema(schema: dict) -> list[dict]:
    """Flatten a DatasetVersion schema map ({col -> {type, nullable, tags}}) into
    the ordered [{name, type, pii_tag?}] shape query-service decodes (BR-12)."""
    columns: list[dict] = []
    for col_name, meta in (schema or {}).items():
        meta = meta or {}
        entry = {"name": col_name, "type": meta.get("type") or "string"}
        pii = next((t for t in (meta.get("tags") or []) if str(t).startswith("pii")), None)
        if pii:
            entry["pii_tag"] = pii
        columns.append(entry)
    return columns


def _dataset_urns(tenant_id: str, dataset: Dataset, versions: list[DatasetVersion]) -> set[str]:
    urns = {dataset_urn(tenant_id, dataset.id)}
    urns.update(version_urn(tenant_id, dataset.id, v.version_no) for v in versions)
    return urns


def _run_to_dict(r: EntityResolutionRun) -> dict:
    return {
        "run_id": r.id, "dataset_id": r.dataset_id, "config_id": r.config_id,
        "entity_type": r.entity_type, "record_count": r.record_count,
        "resolved_entity_count": r.resolved_entity_count,
        "merged_cluster_count": r.merged_cluster_count,
        "review_candidate_count": r.review_candidate_count, "status": r.status,
        "created_by": r.created_by, "created_at": r.created_at,
    }


def _candidate_to_dict(c: EntityMergeCandidate) -> dict:
    return {
        "id": c.id, "run_id": c.run_id, "dataset_id": c.dataset_id,
        "entity_type": c.entity_type, "left_pk": c.left_pk, "right_pk": c.right_pk,
        "score": c.score, "evidence": c.evidence, "status": c.status,
        "proposal_id": c.proposal_id, "decided_by": c.decided_by,
        "decided_at": c.decided_at, "created_at": c.created_at,
    }


# ---------------------------------------------------------------------------


class DatasetService(_Base):
    async def _check_name_free(self, uow: UnitOfWork, workspace_id: str, name: str,
                               exclude_id: str | None = None) -> None:
        existing = await uow.datasets.get_by_name(workspace_id, name)
        if existing and existing.id != exclude_id:
            raise Conflict(f"dataset name {name!r} already exists in workspace")

    async def create(self, ctx: CallCtx, payload: dict) -> Dataset:
        now = self.clock.now()
        # An explicit id is honored (single source of truth for URNs): the
        # ingestion consumer passes the id ingestion-service pre-minted so the
        # dataset row id matches every dataset_urn already handed out. The API
        # path passes no id and one is minted here.
        ds_id = str(payload.get("id") or uuid7())
        tenant_compact = ctx.tenant_id.replace("-", "")[:12]
        dataset = Dataset(
            id=ds_id,
            tenant_id=ctx.tenant_id,
            workspace_id=payload["workspace_id"],
            name=payload["name"],
            description=payload.get("description"),
            visibility=payload.get("visibility") or Visibility.WORKSPACE,
            iceberg_table=payload.get("iceberg_table") or f"bronze.{tenant_compact}.ds_{ds_id}",
            partition_spec=payload.get("partition_spec"),
            tags=payload.get("tags") or [],
            custom_metadata=payload.get("custom_metadata"),
            created_by=ctx.actor.get("id", "unknown"),
            created_at=now,
            updated_at=now,
        )
        async with self.uow(ctx.tenant_id) as uow:
            await self._check_name_free(uow, dataset.workspace_id, dataset.name)
            await uow.datasets.add(dataset)
            await self._emit(
                uow, ctx, "dataset.created", dataset_urn(ctx.tenant_id, ds_id),
                {"name": dataset.name, "workspace_id": dataset.workspace_id},
            )
            await uow.commit()
        await self.deps.search_index.index_dataset(dataset)
        return dataset

    async def get(self, ctx: CallCtx, dataset_id: str) -> tuple[Dataset, DatasetVersion | None]:
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            current = None
            if dataset.current_version_id:
                current = await uow.versions.get_by_id(dataset.current_version_id)
            return dataset, current

    async def resolve(
        self, tenant_id: str, name: str, version: int
    ) -> tuple[Dataset, DatasetVersion, list[str], list[dict]]:
        """Resolve a logical (tenant, name[, version]) to its physical Iceberg
        parquet source for query-service (QRY-FR-005). version <= 0 => latest.

        No caller token: the tenant is supplied by the internal caller
        (query-service) and threaded through the UoW, which sets the RLS
        `app.tenant_id` GUC so the lookup returns the tenant's row. Only
        physical-location metadata is returned; the row data stays RLS-guarded
        when query-service runs the SQL under the end user."""
        # Build an ordered candidate list. Prefer an exact name match, then any
        # dataset whose NORMALIZED relation matches — the semantic compiler emits
        # FROM "main"."<safe_relation>", so query-service auto-materialize resolves
        # the normalized name ("auto_claims_123") while the real name has hyphens
        # ("auto-claims-123"). A REUSED environment can hold several datasets that
        # normalize to the same relation — including broken ones whose Iceberg
        # table was never created (the dev DB role does not FORCE RLS, so prior
        # runs accumulate). Try each candidate, most recent first, and return the
        # first whose physical Iceberg source actually loads; skip the broken ones
        # instead of 500ing. Tenant is matched explicitly (never trust RLS alone).
        target = safe_relation(name)
        async with self.uow(tenant_id) as uow:
            seen: set = set()
            candidates: list[Dataset] = []
            exact = await uow.datasets.get_by_name_in_tenant(name)
            if exact and exact.tenant_id == tenant_id:
                candidates.append(exact)
                seen.add(exact.id)
            for cand in await uow.datasets.all_active():
                if (
                    cand.tenant_id == tenant_id
                    and cand.id not in seen
                    and safe_relation(cand.name) == target
                ):
                    candidates.append(cand)
                    seen.add(cand.id)
            candidates.sort(key=lambda x: (x.created_at or ""), reverse=True)
            resolved: list[tuple] = []
            for ds in candidates:
                if version and version > 0:
                    dsv = await uow.versions.get(ds.id, version)
                elif ds.current_version_id:
                    dsv = await uow.versions.get_by_id(ds.current_version_id)
                else:
                    dsv = await uow.versions.latest(ds.id)
                if dsv:
                    resolved.append((ds, dsv))
        if not resolved:
            raise NotFound(f"dataset {name!r} not found")
        # Enumerate the exact parquet files for the pinned snapshot from the
        # Iceberg manifest (never a prefix glob). Columns come from the version
        # schema, falling back to the physical table schema when it is empty.
        last_err: Exception | None = None
        for dataset, dsv in resolved:
            try:
                source_uris = await self.deps.catalog.data_file_uris(
                    dataset.iceberg_table, dsv.iceberg_snapshot_id
                )
                columns = _columns_from_schema(dsv.schema)
                if not columns:
                    columns = await self.deps.catalog.table_columns(dataset.iceberg_table)
                return dataset, dsv, source_uris, columns
            except Exception as exc:  # noqa: BLE001 — skip broken duplicate datasets
                last_err = exc
                continue
        raise NotFound(f"dataset {name!r} has no loadable physical source ({last_err})")

    async def dataset_detail(
        self, tenant_id: str, dataset_id: str
    ) -> tuple[dict[str, str], str, list[str]]:
        """Internal dataset detail for semantic-service binding validation
        (SEM-FR-002): returns (schema {col->type}, physical_table, primary_key).

        Derives the column schema exactly like ``resolve()`` — from the current
        version's schema, falling back to the physical Iceberg table columns when
        the version schema is empty (bronze is created string-typed from ingest).
        ``physical_table`` = ``main.<safe_relation>`` so it lines up with the
        semantic model's entity.table. Tenant match is enforced as defense in
        depth like ``resolve()``."""
        async with self.uow(tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset or dataset.tenant_id != tenant_id:
                raise NotFound("dataset not found")
            dsv: DatasetVersion | None = None
            if dataset.current_version_id:
                dsv = await uow.versions.get_by_id(dataset.current_version_id)
            else:
                dsv = await uow.versions.latest(dataset.id)
        columns = _columns_from_schema(dsv.schema) if dsv else []
        if not columns:
            columns = await self.deps.catalog.table_columns(dataset.iceberg_table)
        schema_dict = {c["name"]: (c.get("type") or "string") for c in columns}
        physical_table = f"{RESOLVE_NAMESPACE}.{safe_relation(dataset.name)}"
        return schema_dict, physical_table, []

    async def read_rows(
        self, tenant_id: str, dataset_id: str, limit: int = 10000
    ) -> tuple[list[str], list[dict]]:
        """Internal bulk row read (DST-FR internal): materialize a dataset's
        current-version rows from its pinned Iceberg snapshot, bounded by ``limit``.

        Used by the pipeline-orchestrator to feed a ``read-from-warehouse`` node
        real dataset rows (data inputs). Tenant match is enforced as defense in
        depth like ``resolve()``/``dataset_detail()``. NaN -> None so the payload
        is JSON-serialisable."""
        async with self.uow(tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset or dataset.tenant_id != tenant_id:
                raise NotFound("dataset not found")
            if dataset.current_version_id:
                dsv = await uow.versions.get_by_id(dataset.current_version_id)
            else:
                dsv = await uow.versions.latest(dataset.id)
        if not dsv:
            raise NotFound("dataset has no readable version")
        if limit and limit > 0:
            # Bound the read at the engine so a large snapshot is never fully
            # materialized just to return the first `limit` rows.
            df = await self.deps.catalog.read_snapshot_head(
                dataset.iceberg_table, dsv.iceberg_snapshot_id, limit
            )
        else:
            df = await self.deps.catalog.read_snapshot(
                dataset.iceberg_table, dsv.iceberg_snapshot_id
            )
        import pandas as pd

        columns = [str(c) for c in df.columns]
        rows = df.where(pd.notna(df), None).to_dict(orient="records")
        return columns, rows

    async def resolve_entities(
        self, tenant_id: str, dataset_id: str, *, config: dict, pk_column: str,
        row_limit: int = 20000, persist: bool = False, ctx: CallCtx | None = None,
        created_by: str | None = None,
    ) -> dict:
        """BRD 56: run first-party entity resolution over a dataset's real rows
        and return the governed resolved-entity view (clusters + lineage) + the
        below-auto merge candidates a steward reviews (four-eyes). Read-only over
        the SOURCE — it links records, never mutates them (ER-FR-050).

        inc2: when ``persist`` is set the config is versioned (ER-FR-001), and the
        run + resolved clusters + member lineage + merge candidates are stored
        (ER-FR-010/040) so decisions can read them and stewards can review merges.
        """
        from app.domain.entity_resolution import ResolutionConfig, ScoringField, resolve

        columns, rows = await self.read_rows(tenant_id, dataset_id, row_limit)
        if pk_column not in columns:
            raise ValidationFailed(f"pk_column '{pk_column}' is not a column of this dataset")

        def _cols(names):
            missing = [c for c in names if c not in columns]
            if missing:
                raise ValidationFailed(f"unknown column(s): {', '.join(missing)}")
            return list(names)

        det_keys = [_cols(k) for k in (config.get("deterministic_keys") or [])]
        scoring = [ScoringField(column=_cols([f["column"]])[0], weight=float(f.get("weight", 1.0)))
                   for f in (config.get("scoring_fields") or [])]
        blocking = _cols(config.get("blocking_fields") or [])
        if not det_keys and not scoring:
            raise ValidationFailed("config needs at least one deterministic_key or scoring_field")

        entity_type = str(config.get("entity_type") or "entity")
        auto_thr = float(config.get("auto_merge_threshold", 0.85))
        review_thr = float(config.get("review_threshold", 0.60))
        cfg = ResolutionConfig(
            entity_type=entity_type, deterministic_keys=det_keys, scoring_fields=scoring,
            blocking_fields=blocking, auto_merge_threshold=auto_thr, review_threshold=review_thr)
        result = resolve(rows, cfg, pk_column=pk_column)

        multi = [c for c in result.clusters if len(c.member_pks) > 1]
        out = {
            "dataset_id": dataset_id,
            "entity_type": entity_type,
            "record_count": len(rows),
            "resolved_entity_count": len(result.clusters),
            "merged_cluster_count": len(multi),
            "review_candidate_count": len(result.merge_candidates),
            "clusters": [
                {"resolved_entity_id": c.resolved_entity_id, "member_pks": c.member_pks,
                 "confidence": c.confidence, "method": c.method, "evidence": c.evidence}
                for c in result.clusters],
            "merge_candidates": [
                {"left_pk": m.left_pk, "right_pk": m.right_pk, "score": m.score,
                 "evidence": m.evidence} for m in result.merge_candidates],
        }
        if not persist:
            return out

        now = self.clock.now()
        actor_sub = created_by or (ctx.actor.get("id") if ctx else None) or "system"
        async with self.uow(tenant_id) as uow:
            repo = uow.entity_resolution
            version_no = await repo.next_config_version(dataset_id, entity_type)
            config_id = str(uuid7())
            await repo.add_config(EntityResolutionConfig(
                id=config_id, tenant_id=tenant_id, dataset_id=dataset_id,
                entity_type=entity_type, version_no=version_no,
                deterministic_keys=det_keys,
                scoring_fields=[{"column": f.column, "weight": f.weight} for f in scoring],
                blocking_fields=blocking, auto_merge_threshold=auto_thr,
                review_threshold=review_thr, pk_column=pk_column,
                created_by=actor_sub, created_at=now))

            run_id = str(uuid7())
            await repo.add_run(EntityResolutionRun(
                id=run_id, tenant_id=tenant_id, dataset_id=dataset_id, config_id=config_id,
                entity_type=entity_type, record_count=len(rows),
                resolved_entity_count=len(result.clusters), merged_cluster_count=len(multi),
                review_candidate_count=len(result.merge_candidates), status="completed",
                created_by=actor_sub, created_at=now))

            await repo.add_resolved_entities([
                ResolvedEntity(
                    resolved_entity_id=c.resolved_entity_id, run_id=run_id, tenant_id=tenant_id,
                    dataset_id=dataset_id, entity_type=entity_type,
                    member_count=len(c.member_pks), confidence=c.confidence, method=c.method)
                for c in result.clusters])

            members: list[ResolvedEntityMember] = []
            ev_by_pk: dict[str, list[dict]] = {}
            for c in result.clusters:
                for e in c.evidence:
                    ev_by_pk.setdefault(c.resolved_entity_id, [])
            for c in result.clusters:
                for pk in c.member_pks:
                    members.append(ResolvedEntityMember(
                        id=str(uuid7()), resolved_entity_id=c.resolved_entity_id, run_id=run_id,
                        tenant_id=tenant_id, member_pk=pk, method=c.method,
                        evidence=c.evidence if len(c.member_pks) > 1 else []))
            await repo.add_members(members)

            await repo.add_candidates([
                EntityMergeCandidate(
                    id=str(uuid7()), run_id=run_id, tenant_id=tenant_id, dataset_id=dataset_id,
                    entity_type=entity_type, left_pk=m.left_pk, right_pk=m.right_pk,
                    score=m.score, evidence=m.evidence, status=MergeCandidateStatus.PENDING,
                    proposal_id=None, decided_by=None, decided_at=None, created_at=now)
                for m in result.merge_candidates])

            if ctx is not None:
                await self._emit(
                    uow, ctx, "dataset.entity_resolution.run",
                    dataset_urn(tenant_id, dataset_id),
                    {"run_id": run_id, "config_id": config_id, "config_version": version_no,
                     "entity_type": entity_type, "resolved_entity_count": len(result.clusters),
                     "merged_cluster_count": len(multi),
                     "review_candidate_count": len(result.merge_candidates)})
            await uow.commit()

        out.update({"run_id": run_id, "config_id": config_id, "config_version": version_no})
        return out

    async def list_resolution_runs(self, tenant_id: str, dataset_id: str,
                                   limit: int = 50) -> list[dict]:
        """ER-FR-010: prior resolution runs for a dataset (newest first)."""
        async with self.uow(tenant_id) as uow:
            runs = await uow.entity_resolution.list_runs(dataset_id, limit)
        return [_run_to_dict(r) for r in runs]

    async def get_resolution_run(self, tenant_id: str, run_id: str) -> dict:
        """ER-FR-010/040: a run's resolved clusters + member lineage (AC-4)."""
        async with self.uow(tenant_id) as uow:
            run = await uow.entity_resolution.get_run(run_id)
            if run is None:
                raise NotFound(f"resolution run {run_id} not found")
            entities = await uow.entity_resolution.list_resolved_entities(run_id)
            members = await uow.entity_resolution.list_members(run_id)
        members_by_entity: dict[str, list[dict]] = {}
        for m in members:
            members_by_entity.setdefault(m.resolved_entity_id, []).append(
                {"member_pk": m.member_pk, "method": m.method, "evidence": m.evidence})
        return {
            **_run_to_dict(run),
            "clusters": [
                {"resolved_entity_id": e.resolved_entity_id, "member_count": e.member_count,
                 "confidence": e.confidence, "method": e.method,
                 "members": members_by_entity.get(e.resolved_entity_id, [])}
                for e in entities],
        }

    async def list_merge_candidates(self, tenant_id: str, run_id: str,
                                    status: str | None = None) -> list[dict]:
        """ER-FR-030: the merge candidates a steward reviews for a run."""
        async with self.uow(tenant_id) as uow:
            cands = await uow.entity_resolution.list_candidates(run_id, status)
        return [_candidate_to_dict(c) for c in cands]

    async def get_merge_candidate(self, tenant_id: str, candidate_id: str) -> dict:
        async with self.uow(tenant_id) as uow:
            c = await uow.entity_resolution.get_candidate(candidate_id)
            if c is None:
                raise NotFound(f"merge candidate {candidate_id} not found")
        return _candidate_to_dict(c)

    async def link_merge_proposal(self, tenant_id: str, candidate_id: str,
                                  proposal_id: str) -> None:
        """Record the governed proposal a steward opened over a pending candidate
        (ER-FR-030). The confirm itself lands via ``apply_entity_merge`` when the
        four-eyes proposal is approved."""
        async with self.uow(tenant_id) as uow:
            c = await uow.entity_resolution.get_candidate(candidate_id)
            if c is None:
                raise NotFound(f"merge candidate {candidate_id} not found")
            if c.status != MergeCandidateStatus.PENDING:
                raise Conflict(f"merge candidate is already {c.status}")
            await uow.entity_resolution.set_candidate_proposal(candidate_id, proposal_id)
            await uow.commit()

    async def apply_entity_merge(
        self, tenant_id: str, *, candidate_id: str, decided_by: str, approve: bool,
        ctx: CallCtx | None = None,
    ) -> dict:
        """ER-FR-030 execution: the four-eyes proposal for a merge candidate was
        DECIDED. On approve, the candidate is confirmed (link layer only — the SoR
        is never mutated, BR-4/ER-FR-050); on reject it is closed. Idempotent: a
        second decide on a settled candidate is a no-op returning current state."""
        now = self.clock.now()
        async with self.uow(tenant_id) as uow:
            c = await uow.entity_resolution.get_candidate(candidate_id)
            if c is None:
                raise NotFound(f"merge candidate {candidate_id} not found")
            if c.status != MergeCandidateStatus.PENDING:
                return _candidate_to_dict(c)
            new_status = (MergeCandidateStatus.APPROVED if approve
                          else MergeCandidateStatus.REJECTED)
            await uow.entity_resolution.decide_candidate(
                candidate_id, status=new_status, decided_by=decided_by, decided_at=now)
            if ctx is not None:
                await self._emit(
                    uow, ctx, "dataset.entity_resolution.merge_decided",
                    dataset_urn(tenant_id, c.dataset_id),
                    {"candidate_id": candidate_id, "run_id": c.run_id, "status": new_status,
                     "left_pk": c.left_pk, "right_pk": c.right_pk, "decided_by": decided_by})
            await uow.commit()
            settled = await uow.entity_resolution.get_candidate(candidate_id)
        return _candidate_to_dict(settled)

    async def browse_rows(
        self, ctx: CallCtx, dataset_id: str, *, offset: int = 0, limit: int = 50,
        sort_col: str | None = None, sort_dir: str = "asc",
        filters: list[dict] | None = None,
    ) -> dict:
        """User-facing paginated row browse (DST-FR-050): a dataset's current-
        version rows with per-column filtering, single-column sort, and offset
        paging. Filtering + sorting + counting + paging are PUSHED DOWN into
        DuckDB over the snapshot parquet, so they are global + exact and nothing
        larger than the returned page is materialized (regardless of table
        size). Numeric columns compare numerically; only the returned page is
        stringified for display. Returns {columns, rows, total (unfiltered),
        filtered, offset, limit, truncated}."""
        import math

        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset or dataset.tenant_id != ctx.tenant_id:
                raise NotFound("dataset not found")
            if dataset.current_version_id:
                dsv = await uow.versions.get_by_id(dataset.current_version_id)
            else:
                dsv = await uow.versions.latest(dataset.id)
        if not dsv:
            raise NotFound("dataset has no readable version")

        offset = max(0, offset)
        limit = max(1, min(limit, 500))
        columns, page, total, filtered = await self.deps.catalog.browse_snapshot(
            dataset.iceberg_table, dsv.iceberg_snapshot_id,
            filters=filters, sort_col=sort_col, sort_dir=sort_dir,
            offset=offset, limit=limit,
        )

        def _cell(v):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return str(v)

        rows = [[_cell(v) for v in rec] for rec in page]
        return {
            "columns": columns, "rows": rows, "total": total,
            "filtered": filtered, "offset": offset, "limit": limit,
            # Engine-pushed: counts are exact and sort/filter are global, so the
            # result is never a truncated working set. Kept for API compatibility.
            "truncated": False,
        }

    async def list(self, ctx: CallCtx, filters: DatasetFilters, sort: str,
                   limit: int, cursor: str | None) -> Page:
        if filters.q:
            filters.ids = await self.deps.search_index.search(ctx.tenant_id, filters.q)
            if not filters.ids:
                return Page(items=[], next_cursor=None, has_more=False)
        async with self.uow(ctx.tenant_id) as uow:
            return await uow.datasets.list(filters, sort, limit, cursor)

    async def patch(self, ctx: CallCtx, dataset_id: str, changes: dict,
                    if_match: str | None) -> Dataset:
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            if if_match is not None and if_match.strip('"') != etag_for(dataset.updated_at):
                raise Conflict("stale If-Match; dataset was modified concurrently")

            deprecating = changes.get("lifecycle") == Lifecycle.DEPRECATED and (
                dataset.lifecycle != Lifecycle.DEPRECATED
            )
            if "name" in changes and changes["name"] != dataset.name:
                await self._check_name_free(
                    uow, dataset.workspace_id, changes["name"], exclude_id=dataset.id
                )
            if changes.get("successor_urn") and not is_valid_urn(changes["successor_urn"]):
                raise ValidationFailed("successor_urn is not a valid URN")
            if changes.get("custom_metadata") is not None:
                meta = changes["custom_metadata"]
                if len(meta) > 32 or any(len(str(v)) > 1024 for v in meta.values()):
                    raise ValidationFailed("custom_metadata limited to 32 pairs of <=1KB values")

            for key in ("name", "description", "tags", "visibility", "lifecycle",
                        "successor_urn", "custom_metadata", "partition_spec"):
                if key in changes and changes[key] is not None:
                    setattr(dataset, key, changes[key])
            # Strictly monotonic updated_at so the ETag always changes (BR-11)
            dataset.updated_at = max(
                self.clock.now(), dataset.updated_at + timedelta(microseconds=1)
            )
            await uow.datasets.update(dataset)
            event = "dataset.deprecated" if deprecating else "dataset.updated"
            await self._emit(
                uow, ctx, event, dataset_urn(ctx.tenant_id, dataset.id),
                {"changes": sorted(changes.keys()),
                 "successor_urn": dataset.successor_urn} if deprecating
                else {"changes": sorted(changes.keys())},
            )
            await uow.commit()
        await self.deps.search_index.index_dataset(dataset)
        return dataset

    async def consumers_summary(self, ctx: CallCtx, dataset_id: str) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            versions = await uow.versions.list_all(dataset_id)
            urns = _dataset_urns(ctx.tenant_id, dataset, versions)
            graph = await lineage_ops.traverse(
                uow.lineage, urns, direction="downstream", depth=3, activities=None,
                node_cap=self.settings.lineage_node_cap,
            )
        by_service: dict[str, int] = {}
        by_activity: dict[str, int] = {}
        for node in graph.nodes - urns:
            try:
                by_service[parse_urn(node).service] = by_service.get(parse_urn(node).service, 0) + 1
            except ValidationFailed:
                continue
        for edge in graph.edges:
            by_activity[edge.activity] = by_activity.get(edge.activity, 0) + 1
        return {
            "downstream_edges": len(graph.edges),
            "by_service": by_service,
            "by_activity": by_activity,
            "truncated": graph.truncated,
        }

    async def delete(self, ctx: CallCtx, dataset_id: str, force: bool) -> dict:
        summary = await self.consumers_summary(ctx, dataset_id)
        has_consumers = summary["downstream_edges"] > 0
        if has_consumers and not force:
            raise Conflict(
                "dataset has downstream consumers; retry with ?force=true", details=summary
            )
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            dataset.deleted_at = self.clock.now()
            dataset.updated_at = dataset.deleted_at
            await uow.datasets.update(dataset)
            event = "dataset.deleted_with_consumers" if has_consumers else "dataset.deleted"
            await self._emit(
                uow, ctx, event, dataset_urn(ctx.tenant_id, dataset.id),
                {"consumers": summary} if has_consumers else {},
            )
            await uow.commit()
        await self.deps.search_index.remove_dataset(ctx.tenant_id, dataset_id)
        return summary

    async def restore(self, ctx: CallCtx, dataset_id: str) -> Dataset:
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id, include_deleted=True)
            if not dataset:
                raise NotFound("dataset not found")
            if not dataset.deleted_at:
                raise Conflict("dataset is not deleted")
            window = timedelta(days=self.settings.restore_window_days)
            if self.clock.now() - dataset.deleted_at > window:
                raise Gone("restore window has passed")
            # V1 `Copy of` behavior on name conflicts, repeated as needed
            name = dataset.name
            while await uow.datasets.get_by_name(dataset.workspace_id, name):
                name = f"Copy of {name}"
            dataset.name = name
            dataset.deleted_at = None
            dataset.updated_at = self.clock.now()
            await uow.datasets.update(dataset)
            await self._emit(
                uow, ctx, "dataset.restored", dataset_urn(ctx.tenant_id, dataset.id),
                {"name": dataset.name},
            )
            await uow.commit()
        await self.deps.search_index.index_dataset(dataset)
        return dataset

    async def similar(self, ctx: CallCtx, *, schema: dict | None,
                      columns: list[str] | None) -> list[dict]:
        if not schema and not columns:
            raise ValidationFailed("provide either schema or columns")
        async with self.uow(ctx.tenant_id) as uow:
            datasets = await uow.datasets.all_active()
            candidates = []
            for ds in datasets:
                ds_schema: dict = {}
                if ds.current_version_id:
                    version = await uow.versions.get_by_id(ds.current_version_id)
                    ds_schema = version.schema if version else {}
                candidates.append((ds, ds_schema))
        ranked = rank_similar(candidates, columns=columns, schema=schema)
        return [
            {"id": ds.id, "name": ds.name, "urn": dataset_urn(ctx.tenant_id, ds.id),
             "score": score, "matched_columns": matched}
            for ds, score, matched in ranked
        ]


# ---------------------------------------------------------------------------


class VersionService(_Base):
    async def register(self, ctx: CallCtx, dataset_id: str, payload: dict) -> DatasetVersion:
        """Register an immutable version (DST-FR-003, BR-1/BR-2) and kick profiling."""
        snapshot_id = int(payload["iceberg_snapshot_id"])
        skip_profiling = bool(payload.get("skip_profiling"))
        spec: ProfileJobSpec | None = None

        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            if not await self.deps.catalog.snapshot_exists(dataset.iceberg_table, snapshot_id):
                raise Conflict(  # BR-1: DB never points at data that doesn't exist
                    f"iceberg snapshot {snapshot_id} is not committed/readable"
                )
            if await uow.versions.by_snapshot(dataset_id, snapshot_id):
                raise SnapshotAlreadyRegistered(f"snapshot {snapshot_id} already registered")

            version_no = await uow.versions.next_version_no(dataset_id)  # BR-2 advisory lock
            previous = await uow.versions.latest(dataset_id)
            schema = payload.get("schema") or {}
            if not schema:
                # Producers don't always carry a schema in their event/request payload;
                # fall back to the physical Iceberg columns rather than persisting {}
                # forever, mirroring the resilience dataset_detail() already applies
                # at read time (bronze is created string-typed from ingest).
                columns = await self.deps.catalog.table_columns(dataset.iceberg_table)
                schema = {c["name"]: {"type": c.get("type") or "string"} for c in columns}
            if json_size_bytes(schema) > 64 * 1024:
                raise ValidationFailed("schema exceeds 64KB (MASTER-FR-061)")
            schema_diff, breaking = (None, False)
            if previous is not None:
                schema_diff, breaking = compute_schema_diff(previous.schema, schema)

            version = DatasetVersion(
                id=str(uuid7()),
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                version_no=version_no,
                iceberg_snapshot_id=snapshot_id,
                schema=schema,
                schema_diff=schema_diff,
                breaking_change=breaking,
                row_count=payload.get("row_count"),
                bytes=payload.get("bytes"),
                produced_by_urn=payload.get("produced_by_urn"),
                profile_status=ProfileStatus.NONE if skip_profiling else ProfileStatus.PENDING,
                created_at=self.clock.now(),
            )
            await uow.versions.add(version)
            dataset.current_version_id = version.id
            dataset.updated_at = self.clock.now()

            if dataset.status in (DatasetStatus.DRAFT, DatasetStatus.FAILED,
                                  DatasetStatus.READY):
                transition_dataset(dataset, DatasetStatus.PROCESSING)
            if skip_profiling:
                transition_dataset(dataset, DatasetStatus.READY, has_version=True)
            await uow.datasets.update(dataset)

            # Dataset-URN-keyed status event so the dataset detail page (which
            # subscribes on run-status:<dataset-urn>) reflects the
            # DRAFT/FAILED/READY → PROCESSING (or → READY when profiling is
            # skipped) transition live, without a refetch (task #81). The
            # version_created event below is version-URN-keyed and never reaches
            # that subscription.
            await self._emit(
                uow, ctx, "dataset.updated", dataset_urn(ctx.tenant_id, dataset_id),
                {"status": str(dataset.status), "dataset_id": dataset.id},
            )

            v_urn = version_urn(ctx.tenant_id, dataset_id, version_no)
            await self._emit(
                uow, ctx, "dataset.version_created", v_urn,
                {
                    "dataset_urn": dataset_urn(ctx.tenant_id, dataset_id),
                    "version_no": version_no,
                    "iceberg_snapshot_id": snapshot_id,
                    "row_count": version.row_count,
                    "produced_by_urn": version.produced_by_urn,
                    "breaking_change": breaking,
                },
            )
            if breaking:
                await self._emit(
                    uow, ctx, "dataset.schema_changed", v_urn, {"schema_diff": schema_diff}
                )
            if not skip_profiling:
                _, spec = await _create_profile(self, uow, ctx, dataset, version)
            await uow.commit()

        if spec is not None:
            await self.deps.runner_provider().launch(spec)
        return version

    async def list(self, ctx: CallCtx, dataset_id: str, limit: int,
                   cursor: str | None) -> Page:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.datasets.get(dataset_id):
                raise NotFound("dataset not found")
            return await uow.versions.list(dataset_id, limit, cursor)

    async def get(self, ctx: CallCtx, dataset_id: str, version_no: int) -> DatasetVersion:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.datasets.get(dataset_id):
                raise NotFound("dataset not found")
            version = await uow.versions.get(dataset_id, version_no)
            if not version:
                raise NotFound("version not found")
            return version


async def _create_profile(
    svc: _Base, uow: UnitOfWork, ctx: CallCtx, dataset: Dataset, version: DatasetVersion
) -> tuple[Profile, ProfileJobSpec]:
    """Create a pending profile row + job spec (launched by caller after commit)."""
    token = secrets.token_hex(16)
    profile = Profile(
        id=str(uuid7()),
        tenant_id=ctx.tenant_id,
        dataset_id=dataset.id,
        version_id=version.id,
        created_at=svc.clock.now(),
        status=ProfileStatus.PENDING,
        callback_token=token,
        profiler_version=svc.settings.profiler_version,
    )
    await uow.profiles.add(profile)
    version.profile_status = ProfileStatus.PENDING
    version.profile_id = profile.id
    await uow.versions.update(version)
    spec = ProfileJobSpec(
        tenant_id=ctx.tenant_id,
        dataset_id=dataset.id,
        dataset_urn=dataset_urn(ctx.tenant_id, dataset.id),
        version_no=version.version_no,
        profile_id=profile.id,
        iceberg_table=dataset.iceberg_table,
        iceberg_snapshot_id=version.iceberg_snapshot_id,
        sample_strategy="full",
        callback_token=token,
        output_prefix=f"profiles/{ctx.tenant_id}/{dataset.id}/v{version.version_no}",
    )
    return profile, spec


def _merge_profiled_types(schema: dict, summary: dict) -> dict:
    """Overlay the profiler's inferred ``logical_type`` per column onto the
    ingestion-registered schema (which is always all-"string" — bronze is
    contractually string-typed at the Iceberg layer, so ingestion has no better
    answer). Preserves each column's existing ``nullable``/``tags``; a column
    the profile didn't see (e.g. added after profiling) keeps its prior type."""
    merged = {col: dict(meta) for col, meta in (schema or {}).items()}
    for c in summary.get("columns", []) or []:
        name, logical_type = c.get("name"), c.get("logical_type")
        if not name or not logical_type:
            continue
        merged.setdefault(name, {"nullable": True, "tags": []})
        merged[name]["type"] = logical_type
    return merged


def _dataset_summary_metrics(
    summary: dict | None, version: DatasetVersion | None
) -> dict:
    """Render a dataset's profile summary as headline key/value metrics — the
    dataset "metric artifact" chart-service's metric/parameter family fetches via
    GET /api/v1/artifacts (CHART-FR-025). Defensive: when no profile summary is
    available, Rows/Columns fall back to the dataset version's row_count/schema.

    Shape: {"kind": "dataset_summary", "metrics": [{"label", "value"}, ...]}.
    """
    table = (summary or {}).get("table") or {}
    columns = (summary or {}).get("columns") or []

    row_count = table.get("row_count")
    if row_count is None and version is not None:
        row_count = version.row_count
    column_count = table.get("column_count")
    if column_count is None:
        if columns:
            column_count = len(columns)
        elif version is not None and version.schema:
            column_count = len(version.schema)

    metrics: list[dict] = [
        {"label": "Rows", "value": row_count if row_count is not None else 0},
        {"label": "Columns", "value": column_count if column_count is not None else 0},
    ]

    dup_pct = table.get("duplicate_row_pct")
    if dup_pct is not None:
        metrics.append({"label": "Duplicate Rows %", "value": dup_pct})

    null_pcts = [
        c.get("null_pct") for c in columns if c.get("null_pct") is not None
    ]
    if null_pcts:
        avg_null = round(sum(null_pcts) / len(null_pcts), 4)
        metrics.append({"label": "Avg Null %", "value": avg_null})
        metrics.append({"label": "Completeness %", "value": round(100 - avg_null, 4)})

    alerts = (summary or {}).get("alerts")
    if alerts is not None:
        metrics.append({"label": "Alerts", "value": len(alerts)})

    return {"kind": "dataset_summary", "metrics": metrics}


class ProfileService(_Base):
    async def trigger(self, ctx: CallCtx, dataset_id: str, version_no: int) -> Profile:
        """Manual (re)trigger — DST-FR-020; 409 when already running; 429 >3/hour."""
        spec: ProfileJobSpec | None = None
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            version = await uow.versions.get(dataset_id, version_no)
            if not version:
                raise NotFound("version not found")
            if version.profile_status in (ProfileStatus.PENDING, ProfileStatus.RUNNING):
                raise Conflict("a profile is already pending/running for this version")
            hour_ago = self.clock.now() - timedelta(hours=1)
            if (await uow.profiles.count_since(dataset_id, hour_ago)
                    >= self.settings.profile_retrigger_per_hour):
                raise RateLimited("profile re-trigger limited to 3/hour per dataset")
            profile, spec = await _create_profile(self, uow, ctx, dataset, version)
            await uow.commit()
        await self.deps.runner_provider().launch(spec)
        return profile

    async def complete(self, ctx: CallCtx, profile_id: str, body: dict) -> Profile:
        """Profiler result callback (DST-FR-023/024). Signature verified at API layer."""
        relaunch: ProfileJobSpec | None = None
        async with self.uow(ctx.tenant_id) as uow:
            profile = await uow.profiles.get(profile_id)
            if not profile:
                raise NotFound("profile not found")
            if profile.status in (ProfileStatus.COMPLETED, ProfileStatus.FAILED):
                raise Conflict("profile already terminal")
            version = await uow.versions.get_by_id(profile.version_id)
            dataset = await uow.datasets.get(profile.dataset_id, include_deleted=True)
            if version is None or dataset is None:
                raise NotFound("dataset/version for profile not found")

            now = self.clock.now()
            if profile.status == ProfileStatus.PENDING:
                transition_profile(profile, ProfileStatus.RUNNING)
                profile.started_at = profile.started_at or now

            status = body.get("status")
            if status == "completed":
                key_json, key_html = body.get("object_key_json"), body.get("object_key_html")
                if not key_json or not await self.deps.object_store.exists(key_json):
                    raise ValidationFailed("profile.json missing from object storage")
                if not key_html or not await self.deps.object_store.exists(key_html):
                    raise ValidationFailed("profile.html missing from object storage")
                summary = body.get("summary") or {}
                if json_size_bytes(summary) > SUMMARY_MAX_BYTES:
                    raise ValidationFailed("summary exceeds 64KB (BR-4 no-blob rule)")
                transition_profile(profile, ProfileStatus.COMPLETED)
                profile.object_key_json = key_json
                profile.object_key_html = key_html
                profile.summary = summary
                profile.sample = body.get("sample")
                profile.profiler_version = body.get("profiler_version", profile.profiler_version)
                profile.finished_at = now
                version.profile_status = ProfileStatus.COMPLETED
                version.profile_id = profile.id
                # Backfill real column types from the profiler's inference
                # (DST-FR-016 follow-up): ingestion always registers `schema` as
                # all-"string" (bronze is contractually string-typed at the
                # Iceberg layer, per windrose_common.iceberg), so the ONLY place
                # a real logical type is ever computed is here. Without this,
                # every column stays "string" forever and semantic-service's
                # authoring validation (definition.py) rejects legitimate
                # avg()/time-dimension bindings as type mismatches. Merge rather
                # than replace so nullable/tags survive.
                version.schema = _merge_profiled_types(version.schema, summary)
                event = (
                    "dataset.profile_completed",
                    {
                        "profile_id": profile.id,
                        "profile_summary_digest": sha256_hex(str(sorted(summary.items()))),
                        "alerts_count": len(summary.get("alerts", [])),
                    },
                )
            elif status == "failed":
                category = body.get("error_category")
                if category not in set(ProfileErrorCategory):
                    raise ValidationFailed(f"unknown error_category {category!r}")
                retryable = category in (ProfileErrorCategory.TIMEOUT, ProfileErrorCategory.OOM)
                if retryable and profile.attempt == 1:
                    # One automatic retry (§4.3; OOM retry runs at 16GiB in prod)
                    token = secrets.token_hex(16)
                    profile.attempt = 2
                    profile.callback_token = token
                    transition_profile(profile, ProfileStatus.FAILED)
                    transition_profile(profile, ProfileStatus.PENDING)
                    version.profile_status = ProfileStatus.PENDING
                    relaunch = ProfileJobSpec(
                        tenant_id=ctx.tenant_id,
                        dataset_id=dataset.id,
                        dataset_urn=dataset_urn(ctx.tenant_id, dataset.id),
                        version_no=version.version_no,
                        profile_id=profile.id,
                        iceberg_table=dataset.iceberg_table,
                        iceberg_snapshot_id=version.iceberg_snapshot_id,
                        sample_strategy="full",
                        callback_token=token,
                        output_prefix=(
                            f"profiles/{ctx.tenant_id}/{dataset.id}/v{version.version_no}"
                        ),
                    )
                    event = None
                else:
                    transition_profile(profile, ProfileStatus.FAILED)
                    profile.error_category = category
                    profile.finished_at = now
                    version.profile_status = ProfileStatus.FAILED
                    event = ("dataset.profile_failed", {
                        "profile_id": profile.id, "error_category": category,
                    })
            else:
                raise ValidationFailed("status must be 'completed' or 'failed'")

            await uow.profiles.update(profile)
            await uow.versions.update(version)
            # Profile terminal state unblocks the dataset (DST-FR-024: profile
            # failure never blocks usable data — deliberate V1 change).
            if (profile.status in (ProfileStatus.COMPLETED, ProfileStatus.FAILED)
                    and dataset.status == DatasetStatus.PROCESSING):
                transition_dataset(dataset, DatasetStatus.READY, has_version=True)
                dataset.updated_at = now
                await uow.datasets.update(dataset)
                # PROCESSING → READY was previously silent (only the version-URN-
                # keyed profile event fired). Emit a dataset-URN-keyed status
                # event so the subscribed detail page flips to READY live (#81).
                await self._emit(
                    uow, ctx, "dataset.updated", dataset_urn(ctx.tenant_id, dataset.id),
                    {"status": str(dataset.status), "dataset_id": dataset.id},
                )
            if event:
                await self._emit(
                    uow, ctx, event[0],
                    version_urn(ctx.tenant_id, dataset.id, version.version_no), event[1],
                )
            await uow.commit()

        if relaunch is not None:
            await self.deps.runner_provider().launch(relaunch)
        return profile

    async def get_summary(self, ctx: CallCtx, dataset_id: str,
                          version_no: int | None) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            if version_no is not None:
                version = await uow.versions.get(dataset_id, version_no)
            elif dataset.current_version_id:
                version = await uow.versions.get_by_id(dataset.current_version_id)
            else:
                version = None
            if not version or not version.profile_id:
                raise NotFound("no profile for this dataset/version")
            profile = await uow.profiles.get(version.profile_id)
            if not profile:
                raise NotFound("no profile for this dataset/version")

        result: dict = {
            "profile_id": profile.id,
            "status": profile.status,
            "version_no": version.version_no,
            "error_category": profile.error_category,
            "sample": profile.sample,
            "generated_at": profile.finished_at.isoformat() if profile.finished_at else None,
        }
        if profile.summary:
            result.update({
                "table": profile.summary.get("table"),
                "columns": profile.summary.get("columns"),
                "alerts": profile.summary.get("alerts"),
            })
        if profile.status == ProfileStatus.COMPLETED:
            store = self.deps.object_store
            if not await store.exists(profile.object_key_json):
                raise Gone("profile objects have been garbage-collected")
            ttl = self.settings.signed_url_ttl_hours
            result["full_json_url"] = await store.signed_url(profile.object_key_json, ttl)
            result["html_report_url"] = await store.signed_url(profile.object_key_html, ttl)
        return result

    async def metric_artifact(
        self, ctx: CallCtx, dataset_id: str, version_no: int | None
    ) -> dict:
        """Resolve a dataset (version) to its "metric artifact" — the profile
        summary rendered as headline key/value metrics (CHART-FR-025). Primary
        source is the same summary GET /profile serves; when no profile exists
        yet (or its blob was GC'd) Rows/Columns fall back to the version's
        row_count/schema so the metric chart still renders real data."""
        summary: dict | None = None
        try:
            summary = await self.get_summary(ctx, dataset_id, version_no)
        except (NotFound, Gone):
            summary = None
        async with self.uow(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                raise NotFound("dataset not found")
            if version_no is not None:
                version = await uow.versions.get(dataset_id, version_no)
            elif dataset.current_version_id:
                version = await uow.versions.get_by_id(dataset.current_version_id)
            else:
                version = None
        return _dataset_summary_metrics(summary, version)

    async def internal_top_values(
        self, tenant_id: str, dataset_id: str, version_no: int | None = None
    ) -> dict[str, list]:
        """Project the latest REAL profile's per-column top values for the
        internal profile endpoint (SEM-FR-002/080): semantic-service validates
        metric sample values against them.

        Shape: {column_name: [value, ...]} (most frequent first). The profiler
        stores top_values only in the full profile.json blob (the <=64KB
        Postgres summary deliberately omits them — BR-4 no-blob rule), so this
        reads the blob from the object store. Returns {} when no completed
        profile (or its blob) exists yet — never an error, since sample values
        are best-effort enrichment."""
        async with self.uow(tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset or dataset.tenant_id != tenant_id:
                raise NotFound("dataset not found")
            if version_no is not None:
                version = await uow.versions.get(dataset_id, version_no)
            elif dataset.current_version_id:
                version = await uow.versions.get_by_id(dataset.current_version_id)
            else:
                version = await uow.versions.latest(dataset_id)
            if not version or not version.profile_id:
                return {}
            profile = await uow.profiles.get(version.profile_id)
        if (
            not profile
            or profile.status != ProfileStatus.COMPLETED
            or not profile.object_key_json
        ):
            return {}
        store = self.deps.object_store
        if not await store.exists(profile.object_key_json):
            return {}  # blob GC'd (DST-FR-080) — stats live on, samples don't
        import json

        try:
            doc = json.loads(await store.get(profile.object_key_json))
        except Exception:  # noqa: BLE001 — a corrupt blob must not 500 semantic-service
            return {}
        return {
            col["name"]: [tv["value"] for tv in col.get("top_values") or [] if "value" in tv]
            for col in doc.get("columns") or []
            if col.get("name") and col.get("top_values")
        }

    async def sweep_timeouts(self, ctx: CallCtx) -> int:
        """Kill profiles stuck past the 30-min budget (AC-3). Returns count acted on."""
        acted = 0
        relaunches: list[ProfileJobSpec] = []
        cutoff = self.clock.now() - timedelta(minutes=self.settings.profile_timeout_minutes)
        async with self.uow(ctx.tenant_id) as uow:
            stuck = await uow.profiles.running_started_before(cutoff)
            for profile in stuck:
                await self.deps.runner_provider().kill(profile.id)
                version = await uow.versions.get_by_id(profile.version_id)
                dataset = await uow.datasets.get(profile.dataset_id, include_deleted=True)
                if version is None or dataset is None:
                    continue
                acted += 1
                if profile.status == ProfileStatus.PENDING:
                    transition_profile(profile, ProfileStatus.RUNNING)
                if profile.attempt == 1:
                    token = secrets.token_hex(16)
                    profile.attempt = 2
                    profile.callback_token = token
                    profile.started_at = self.clock.now()
                    transition_profile(profile, ProfileStatus.FAILED)
                    transition_profile(profile, ProfileStatus.PENDING)
                    version.profile_status = ProfileStatus.PENDING
                    relaunches.append(ProfileJobSpec(
                        tenant_id=ctx.tenant_id,
                        dataset_id=dataset.id,
                        dataset_urn=dataset_urn(ctx.tenant_id, dataset.id),
                        version_no=version.version_no,
                        profile_id=profile.id,
                        iceberg_table=dataset.iceberg_table,
                        iceberg_snapshot_id=version.iceberg_snapshot_id,
                        sample_strategy="full",
                        callback_token=token,
                        output_prefix=(
                            f"profiles/{ctx.tenant_id}/{dataset.id}/v{version.version_no}"
                        ),
                    ))
                else:
                    transition_profile(profile, ProfileStatus.FAILED)
                    profile.error_category = ProfileErrorCategory.TIMEOUT
                    profile.finished_at = self.clock.now()
                    version.profile_status = ProfileStatus.FAILED
                    if dataset.status == DatasetStatus.PROCESSING:
                        transition_dataset(dataset, DatasetStatus.READY, has_version=True)
                        await uow.datasets.update(dataset)
                    await self._emit(
                        uow, ctx, "dataset.profile_failed",
                        version_urn(ctx.tenant_id, dataset.id, version.version_no),
                        {"profile_id": profile.id,
                         "error_category": ProfileErrorCategory.TIMEOUT},
                    )
                await uow.profiles.update(profile)
                await uow.versions.update(version)
            await uow.commit()
        for spec in relaunches:
            await self.deps.runner_provider().launch(spec)
        return acted


# ---------------------------------------------------------------------------


class LineageService(_Base):
    async def add_edge(self, ctx: CallCtx, payload: dict) -> tuple[LineageEdge, bool]:
        from_urn, to_urn = payload.get("from_urn", ""), payload.get("to_urn", "")
        from_parsed, to_parsed = parse_urn(from_urn), parse_urn(to_urn)  # 422 on bad URN
        activity = payload.get("activity")
        if activity not in set(Activity):
            raise ValidationFailed(f"invalid activity {activity!r}")
        for parsed, urn in ((from_parsed, from_urn), (to_parsed, to_urn)):
            if parsed.tenant != ctx.tenant_id:
                await self._audit_cross_tenant(ctx, urn, "lineage edge write with foreign tenant")
                raise NotFound("resource not found")  # BR-6 / MASTER-FR-003
        if from_urn == to_urn:
            raise ValidationFailed("self-edge rejected (BR-7)")

        async with self.uow(ctx.tenant_id) as uow:
            if await lineage_ops.would_create_cycle(
                uow.lineage, from_urn, to_urn,
                max_depth=self.settings.lineage_max_depth,
                node_cap=self.settings.lineage_node_cap,
            ):
                raise ValidationFailed("edge would create a cycle (lineage must stay a DAG)")
            edge = LineageEdge(
                id=str(uuid7()),
                tenant_id=ctx.tenant_id,
                from_urn=from_urn,
                to_urn=to_urn,
                activity=activity,
                run_urn=payload.get("run_urn"),
                properties=payload.get("properties"),
                actor=ctx.actor,
                occurred_at=payload.get("occurred_at") or self.clock.now(),
                created_at=self.clock.now(),
            )
            edge, created = await uow.lineage.upsert(edge)
            if created:
                await self._emit(
                    uow, ctx, "lineage.edge_created", to_urn,
                    {"from_urn": from_urn, "to_urn": to_urn, "activity": activity,
                     "run_urn": edge.run_urn},
                )
            await uow.commit()
        return edge, created

    async def query(self, ctx: CallCtx, *, urn: str, direction: str, depth: int,
                    activities: list[str] | None) -> dict:
        if depth < 1 or depth > self.settings.lineage_max_depth:
            raise ValidationFailed(
                f"depth must be 1..{self.settings.lineage_max_depth}"
            )
        if direction not in ("upstream", "downstream", "both"):
            raise ValidationFailed("direction must be upstream|downstream|both")
        parsed = parse_urn(urn)
        if parsed.tenant != ctx.tenant_id:
            await self._audit_cross_tenant(ctx, urn, "lineage query with foreign tenant urn")
            raise NotFound("resource not found")
        if activities:
            bad = [a for a in activities if a not in set(Activity)]
            if bad:
                raise ValidationFailed(f"invalid activities: {bad}")

        async with self.uow(ctx.tenant_id) as uow:
            start = {urn}
            # A dataset URN implicitly spans its version URNs (DST-FR-004 semantics)
            if parsed.service == "dataset" and parsed.rtype == "dataset":
                versions = await uow.versions.list_all(parsed.rid)
                start.update(
                    version_urn(ctx.tenant_id, parsed.rid, v.version_no) for v in versions
                )
            graph = await lineage_ops.traverse(
                uow.lineage, start, direction=direction, depth=depth,
                activities=activities, node_cap=self.settings.lineage_node_cap,
            )
            nodes = [await self._enrich(uow, ctx, n) for n in sorted(graph.nodes)]
        return {
            "nodes": nodes,
            "edges": [
                {
                    "from_urn": e.from_urn, "to_urn": e.to_urn, "activity": e.activity,
                    "run_urn": e.run_urn, "occurred_at": e.occurred_at.isoformat(),
                }
                for e in graph.edges
            ],
            "truncated": graph.truncated,
        }

    async def _enrich(self, uow: UnitOfWork, ctx: CallCtx, urn: str) -> dict:
        """DST-FR-043: enrich owned URNs; bare foreign URNs otherwise."""
        try:
            parsed = parse_urn(urn)
        except ValidationFailed:
            return {"urn": urn, "kind": "foreign"}
        if parsed.tenant == ctx.tenant_id and parsed.service == "dataset":
            if parsed.rtype == "dataset":
                dataset = await uow.datasets.get(parsed.rid, include_deleted=True)
                if dataset:
                    return {"urn": urn, "kind": "dataset", "name": dataset.name,
                            "status": dataset.status}
            elif parsed.rtype == "version":
                ref = parse_version_urn(parsed)
                if ref:
                    dataset = await uow.datasets.get(ref[0], include_deleted=True)
                    if dataset:
                        return {"urn": urn, "kind": "version", "name": dataset.name,
                                "version_no": ref[1], "status": dataset.status}
        return {"urn": urn, "kind": "foreign"}


# ---------------------------------------------------------------------------


class RetentionService(_Base):
    def _policy(self) -> RetentionPolicy:
        s = self.settings
        return RetentionPolicy(
            keep_all_days=s.retention_keep_all_days,
            keep_last=s.retention_keep_last,
            monthly_months=s.retention_monthly_months,
            trained_pin_days=s.retention_trained_pin_days,
        )

    async def run_for_tenant(self, ctx: CallCtx,
                             policy: RetentionPolicy | None = None) -> dict:
        """Expire versions per policy (DST-FR-080/081) + purge lapsed soft-deletes."""
        policy = policy or self._policy()
        now = self.clock.now()
        expired_count, purged_count = 0, 0

        async with self.uow(ctx.tenant_id) as uow:
            pin_cutoff = now - timedelta(days=policy.trained_pin_days)
            trained = await uow.lineage.trained_edges_since(pin_cutoff)
            pinned_refs: set[tuple[str, int]] = set()
            for edge in trained:
                for urn_str in (edge.from_urn, edge.to_urn):
                    try:
                        ref = parse_version_urn(parse_urn(urn_str))
                    except ValidationFailed:
                        ref = None
                    if ref:
                        pinned_refs.add(ref)

            for dataset in await uow.datasets.all_active():
                versions = await uow.versions.list_all(dataset.id)
                pinned_ids = {
                    v.id for v in versions if (dataset.id, v.version_no) in pinned_refs
                }
                for version in select_expirable(
                    versions, now=now, policy=policy,
                    current_version_id=dataset.current_version_id,
                    pinned_version_ids=pinned_ids,
                ):
                    await self.deps.catalog.expire_snapshot(
                        dataset.iceberg_table, version.iceberg_snapshot_id
                    )
                    await self._delete_profile_objects(uow, version)
                    version.expired = True  # row survives: schema + stats remain
                    await uow.versions.update(version)
                    await self._emit(
                        uow, ctx, "dataset.version_expired",
                        version_urn(ctx.tenant_id, dataset.id, version.version_no),
                        {"version_no": version.version_no},
                    )
                    expired_count += 1

            # Hard cleanup of soft-deleted datasets past the restore window (DST-FR-006)
            cutoff = now - timedelta(days=self.settings.restore_window_days)
            for dataset in await uow.datasets.soft_deleted_before(cutoff):
                for version in await uow.versions.list_all(dataset.id):
                    await self._delete_profile_objects(uow, version)
                await self.deps.catalog.drop_table(dataset.iceberg_table)
                await uow.datasets.hard_delete(dataset.id)  # lineage survives (BR-8)
                purged_count += 1
            await uow.commit()
        return {"expired_versions": expired_count, "purged_datasets": purged_count}

    async def _delete_profile_objects(self, uow: UnitOfWork, version) -> None:
        if not version.profile_id:
            return
        profile = await uow.profiles.get(version.profile_id)
        if not profile:
            return
        for key in (profile.object_key_json, profile.object_key_html):
            if key:
                await self.deps.object_store.delete(key)

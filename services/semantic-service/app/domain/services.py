"""Application services: models, versioning workflow, compile, verified queries,
bootstrap. Every mutation writes its event to the outbox inside the same unit of
work (MASTER-FR-034)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.compiler.chart import map_chart_request
from app.compiler.compiler import Compiled, Compiler, normalize_request
from app.config import Settings
from app.domain.bootstrap import BootstrapDeriver
from app.domain.definition import (
    Definition,
    compute_diff,
    parse_definition,
    validate_definition,
)
from app.domain.entities import (
    CompileLogEntry,
    ModelVersion,
    Operation,
    SemanticModel,
    VerifiedQuery,
)
from app.domain.errors import (
    Conflict,
    ModelNotPublished,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.ports import Page, UnitOfWork, UowFactory
from app.domain.sqlguard import referenced_words, validate_read_only_sql
from app.domain.state import check_version_transition, check_vq_transition
from app.domain.urn import model_urn, verified_query_urn, version_urn
from app.events.envelope import make_envelope
from app.utils import Clock, sha256_hex, uuid7

VALID_CALLERS = ("api", "chart", "agent_tool")


@dataclass(slots=True)
class CallCtx:
    tenant_id: str
    actor: dict
    via_agent: dict | None = None
    trace_id: str | None = None
    subject: str | None = None
    is_agent: bool = False


@dataclass(slots=True)
class ServiceDeps:
    settings: Settings
    clock: Clock
    uow_factory: UowFactory
    dataset_client: object
    query_client: object
    embeddings: object


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

    async def _audit_cross_tenant(self, ctx: CallCtx, resource_urn: str,
                                  detail: str) -> None:
        """MASTER-FR-003: audit denied access in its own committed uow. A by-id
        miss is indistinguishable from a foreign tenant's id under RLS, so every
        by-id miss is audited (404 either way — no existence leak)."""
        async with self.uow(ctx.tenant_id) as uow:
            await self._emit(uow, ctx, "security.cross_tenant_denied", resource_urn,
                             {"detail": detail})
            await uow.commit()

    async def _get_model(self, ctx: CallCtx, uow: UnitOfWork,
                         model_id: str) -> SemanticModel:
        model = await uow.models.get(model_id)
        if model is None:
            await self._audit_cross_tenant(
                ctx, model_urn(ctx.tenant_id, model_id), "model not visible in tenant")
            raise NotFound("model not found")
        return model

    async def _resolve_model(self, ctx: CallCtx, uow: UnitOfWork, model_ref: str,
                             workspace_id: str | None) -> SemanticModel:
        """Model by id (uuid) or by (workspace, name)."""
        try:
            uuid.UUID(str(model_ref))
            is_uuid = True
        except (ValueError, AttributeError, TypeError):
            is_uuid = False
        if is_uuid:
            return await self._get_model(ctx, uow, str(model_ref))
        if not workspace_id:
            raise ValidationFailed("workspace_id required when model is named by name")
        model = await uow.models.get_by_name(workspace_id, str(model_ref))
        if model is None:
            raise NotFound("model not found")
        return model


def _broken_names(model: SemanticModel) -> set[str]:
    health = model.health or {}
    if health.get("status") != "broken":
        return set()
    return {r["name"] for r in health.get("broken_refs", [])
            if r.get("object_type") in ("measure", "dimension")}


def _version_payload(v: ModelVersion) -> dict:
    return {
        "id": v.id, "model_id": v.model_id, "version_no": v.version_no,
        "status": v.status, "definition": v.definition, "diff": v.diff,
        "submitted_by": v.submitted_by, "approved_by": v.approved_by,
        "decision_note": v.decision_note,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "created_at": v.created_at.isoformat(),
    }


def model_payload(m: SemanticModel, published_version_no: int | None = None) -> dict:
    return {
        "id": m.id, "workspace_id": m.workspace_id, "name": m.name,
        "description": m.description,
        "published_version_id": m.published_version_id,
        "published_version_no": published_version_no,
        "health": m.health or {"status": "ok", "broken_refs": []},
        "created_by": m.created_by,
        "created_at": m.created_at.isoformat(), "updated_at": m.updated_at.isoformat(),
    }


def vq_payload(vq: VerifiedQuery) -> dict:
    return {
        "id": vq.id, "workspace_id": vq.workspace_id, "model_id": vq.model_id,
        "nl_text": vq.nl_text, "sql_text": vq.sql_text, "variables": vq.variables,
        "status": vq.status, "tags": vq.tags, "provenance": vq.provenance,
        "health_note": vq.health_note, "submitted_by": vq.submitted_by,
        "approved_by": vq.approved_by,
        "decided_at": vq.decided_at.isoformat() if vq.decided_at else None,
        "created_at": vq.created_at.isoformat(), "updated_at": vq.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------


class ModelService(_Base):
    async def create(self, ctx: CallCtx, body: dict) -> tuple[SemanticModel, ModelVersion]:
        now = self.clock.now()
        definition = body.get("definition") or {}
        parse_definition(definition, settings=self.settings)  # save-time validation
        async with self.uow(ctx.tenant_id) as uow:
            existing = await uow.models.get_by_name(body["workspace_id"], body["name"])
            if existing:
                raise Conflict(f"model name {body['name']!r} already exists in workspace")
            model = SemanticModel(
                id=str(uuid7()), tenant_id=ctx.tenant_id,
                workspace_id=body["workspace_id"], name=body["name"],
                description=body.get("description"), published_version_id=None,
                health={"status": "ok", "broken_refs": []},
                created_by=ctx.subject or ctx.actor.get("id", ""),
                created_at=now, updated_at=now,
            )
            version = ModelVersion(
                id=str(uuid7()), tenant_id=ctx.tenant_id, model_id=model.id,
                version_no=1, status="draft", definition=definition, diff=None,
                submitted_by=None, approved_by=None, decision_note=None,
                published_at=None, created_at=now,
            )
            await uow.models.add(model)
            await uow.versions.add(version)
            await self._emit(uow, ctx, "model.created",
                             model_urn(ctx.tenant_id, model.id),
                             {"name": model.name, "workspace_id": model.workspace_id})
            await uow.commit()
            return model, version

    async def list(self, ctx: CallCtx, workspace_id: str | None, limit: int,
                   cursor: str | None) -> Page:
        async with self.uow(ctx.tenant_id) as uow:
            return await uow.models.list(workspace_id, limit, cursor)

    async def get(self, ctx: CallCtx, model_id: str) -> tuple[SemanticModel, int | None]:
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            published_no = None
            if model.published_version_id:
                pv = await uow.versions.get_by_id(model.published_version_id)
                published_no = pv.version_no if pv else None
            return model, published_no

    async def patch(self, ctx: CallCtx, model_id: str, patch: dict) -> SemanticModel:
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            if "name" in patch and patch["name"] != model.name:
                existing = await uow.models.get_by_name(model.workspace_id, patch["name"])
                if existing and existing.id != model.id:
                    raise Conflict(f"model name {patch['name']!r} already exists")
                model.name = patch["name"]
            if "description" in patch:
                model.description = patch["description"]
            model.updated_at = self.clock.now()
            await uow.models.update(model)
            await self._emit(uow, ctx, "model.updated",
                             model_urn(ctx.tenant_id, model.id),
                             {"fields": sorted(set(patch))})
            await uow.commit()
            return model

    async def delete(self, ctx: CallCtx, model_id: str) -> None:
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            model.deleted_at = self.clock.now()
            model.updated_at = model.deleted_at
            await uow.models.update(model)
            await self._emit(uow, ctx, "model.deleted",
                             model_urn(ctx.tenant_id, model.id), {"name": model.name})
            await uow.commit()


class VersionService(_Base):
    def __init__(self, deps: ServiceDeps, compile_service: CompileService | None = None):
        super().__init__(deps)
        self.compile_service = compile_service

    async def list(self, ctx: CallCtx, model_id: str, limit: int,
                   cursor: str | None) -> Page:
        async with self.uow(ctx.tenant_id) as uow:
            await self._get_model(ctx, uow, model_id)
            return await uow.versions.list(model_id, limit, cursor)

    async def get(self, ctx: CallCtx, model_id: str, version_no: int) -> ModelVersion:
        async with self.uow(ctx.tenant_id) as uow:
            await self._get_model(ctx, uow, model_id)
            version = await uow.versions.get(model_id, version_no)
            if version is None:
                raise NotFound("version not found")
            return version

    async def create_draft(self, ctx: CallCtx, model_id: str) -> ModelVersion:
        """New draft seeded from the published definition (BRD §5)."""
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            open_version = await uow.versions.open_version(model_id)
            if open_version is not None:
                raise Conflict(
                    f"version {open_version.version_no} is {open_version.status}; "
                    "finish it before opening a new draft")
            latest = await uow.versions.latest(model_id)
            seed: dict = {}
            if model.published_version_id:
                published = await uow.versions.get_by_id(model.published_version_id)
                if published:
                    seed = published.definition
            version = ModelVersion(
                id=str(uuid7()), tenant_id=ctx.tenant_id, model_id=model_id,
                version_no=(latest.version_no if latest else 0) + 1, status="draft",
                definition=seed, diff=None, submitted_by=None, approved_by=None,
                decision_note=None, published_at=None, created_at=self.clock.now(),
            )
            await uow.versions.add(version)
            await uow.commit()
            return version

    async def patch_draft(self, ctx: CallCtx, model_id: str, version_no: int,
                          definition: dict) -> ModelVersion:
        parse_definition(definition, settings=self.settings)  # SEM-FR-006 at save
        async with self.uow(ctx.tenant_id) as uow:
            await self._get_model(ctx, uow, model_id)
            version = await uow.versions.get(model_id, version_no)
            if version is None:
                raise NotFound("version not found")
            if version.status == "rejected":  # rejected -> draft on revise (§4.2)
                check_version_transition("rejected", "draft")
                version.status = "draft"
            if version.status != "draft":
                raise Conflict(f"version is {version.status}; only drafts are editable")
            version.definition = definition
            await uow.versions.update(version)
            await uow.commit()
            return version

    async def _validate_full(self, ctx: CallCtx, definition: dict) -> Definition:
        defn = parse_definition(definition, settings=self.settings)
        lookups: dict[str, dict | None] = {}
        for entity in defn.entities.values():
            if entity.dataset_urn not in lookups:
                lookups[entity.dataset_urn] = await self.deps.dataset_client.get_dataset(
                    ctx.tenant_id, entity.dataset_urn)
        problems = validate_definition(defn, lambda urn: lookups.get(urn))
        if problems:
            raise ValidationFailed("definition validation failed", problems)
        return defn

    async def submit(self, ctx: CallCtx, model_id: str, version_no: int) -> ModelVersion:
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            version = await uow.versions.get(model_id, version_no)
            if version is None:
                raise NotFound("version not found")
            check_version_transition(version.status, "in_review")
            await self._validate_full(ctx, version.definition)
            version.status = "in_review"
            version.submitted_by = ctx.subject
            await uow.versions.update(version)
            await self._emit(uow, ctx, "model.version_submitted",
                             version_urn(ctx.tenant_id, model.id, version_no),
                             {"version_no": version_no})
            await uow.commit()
            return version

    async def approve(self, ctx: CallCtx, model_id: str, version_no: int,
                      note: str | None = None) -> ModelVersion:
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            await uow.versions.lock_model(model_id)  # BR-10
            version = await uow.versions.get(model_id, version_no)
            if version is None:
                raise NotFound("version not found")
            check_version_transition(version.status, "published")
            if version.submitted_by and version.submitted_by == ctx.subject:
                raise PermissionDenied(
                    "author cannot approve their own version (SEM-FR-007)")
            # Publication guard: bindings must still be valid
            defn = await self._validate_full(ctx, version.definition)

            previous_definition = None
            if model.published_version_id:
                previous = await uow.versions.get_by_id(model.published_version_id)
                if previous:
                    check_version_transition(previous.status, "superseded")
                    previous.status = "superseded"
                    await uow.versions.update(previous)
                    previous_definition = previous.definition

            now = self.clock.now()
            diff = compute_diff(previous_definition, version.definition)
            version.status = "published"
            version.approved_by = ctx.subject
            version.decision_note = note
            version.diff = diff
            version.published_at = now
            await uow.versions.update(version)
            await uow.versions.rebuild_projections(version)

            model.published_version_id = version.id
            model.health = {"status": "ok", "broken_refs": []}
            model.updated_at = now
            await uow.models.update(model)

            await self._emit(uow, ctx, "model.version_published",
                             version_urn(ctx.tenant_id, model.id, version_no),
                             {"version_no": version_no, "diff": diff})
            await self._emit_deprecations(uow, ctx, model, defn, previous_definition)
            await self._revalidate_verified_queries(uow, ctx, model, diff)
            await uow.commit()

        if self.compile_service is not None:  # SEM-FR-025 cache invalidation
            self.compile_service.invalidate_model(model_id)
        return version

    async def _emit_deprecations(self, uow, ctx, model, defn: Definition,
                                 previous_definition: dict | None) -> None:
        previously = {
            m["name"] for m in ((previous_definition or {}).get("measures") or [])
            if m.get("deprecated")
        }
        for measure in defn.measures.values():
            if measure.deprecated and measure.name not in previously:
                charts = await uow.chart_refs.charts_referencing(measure.name)
                await self._emit(
                    uow, ctx, "measure.deprecated",
                    model_urn(ctx.tenant_id, model.id),
                    {"measure": measure.name, "successor": measure.successor,
                     "impacted_charts": [c.chart_urn for c in charts]})

    async def _revalidate_verified_queries(self, uow, ctx, model, diff: dict) -> None:
        """SEM-FR-043: approved queries referencing removed objects -> pending_review."""
        removed = set()
        for kind in ("measures", "dimensions"):
            removed.update(diff.get("removed", {}).get(kind, []))
        if not removed:
            return
        for vq in await uow.verified_queries.approved_for_model(model.id):
            hit = sorted(removed & referenced_words(vq.sql_text))
            if hit:
                check_vq_transition(vq.status, "pending_review")
                vq.status = "pending_review"
                vq.health_note = f"model publish removed: {', '.join(hit)}"
                vq.updated_at = self.clock.now()
                await uow.verified_queries.update(vq)
                await self._emit(uow, ctx, "verified_query.submitted",
                                 verified_query_urn(ctx.tenant_id, vq.id),
                                 {"reason": "revalidation", "health_note": vq.health_note})

    async def reject(self, ctx: CallCtx, model_id: str, version_no: int,
                     note: str | None) -> ModelVersion:
        if not note:
            raise ValidationFailed("a decision note is required to reject")
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            version = await uow.versions.get(model_id, version_no)
            if version is None:
                raise NotFound("version not found")
            check_version_transition(version.status, "rejected")
            if version.submitted_by and version.submitted_by == ctx.subject:
                raise PermissionDenied("author cannot review their own version")
            version.status = "rejected"
            version.approved_by = ctx.subject
            version.decision_note = note
            await uow.versions.update(version)
            await self._emit(uow, ctx, "model.version_rejected",
                             version_urn(ctx.tenant_id, model.id, version_no),
                             {"version_no": version_no, "note": note})
            await uow.commit()
            return version

    async def get_definition(self, ctx: CallCtx, model_id: str,
                             version_no: int | None) -> tuple[dict, int]:
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._get_model(ctx, uow, model_id)
            if version_no is not None:
                version = await uow.versions.get(model_id, version_no)
                if version is None:
                    raise NotFound("version not found")
            else:
                if not model.published_version_id:
                    raise ModelNotPublished(f"model {model.name!r} has no published version")
                version = await uow.versions.get_by_id(model.published_version_id)
                if version is None:
                    raise NotFound("published version not found")
            return version.definition, version.version_no


# ---------------------------------------------------------------------------


class CompileService(_Base):
    """SEM-FR-020..026. One compiler serves /compile, /compile/chart and the
    MCP tool — the SEM-FR-081 byte-identity guarantee is structural."""

    def __init__(self, deps: ServiceDeps):
        super().__init__(deps)
        self._cache: dict[tuple, dict] = {}

    def invalidate_model(self, model_id: str) -> None:
        self._cache = {k: v for k, v in self._cache.items() if k[0] != model_id}

    async def compile(self, ctx: CallCtx, body: dict, *, caller_class: str = "api",
                      draft_version_no: int | None = None, validate: bool = False,
                      limit_ceiling: int | None = None, token: str | None = None) -> dict:
        started = time.monotonic()
        req = normalize_request(body, self.settings)  # regex gates BEFORE model lookup
        dialect = body.get("dialect") or "trino"
        warnings_extra: list[str] = []
        if limit_ceiling is not None and (req.limit is None or req.limit > limit_ceiling):
            req.limit = limit_ceiling
            warnings_extra.append(f"LIMIT_CLAMPED: agent ceiling {limit_ceiling} applied")

        model_ref = body.get("model")
        if not model_ref:
            raise ValidationFailed("model is required")
        async with self.uow(ctx.tenant_id) as uow:
            model = await self._resolve_model(ctx, uow, model_ref,
                                              body.get("workspace_id"))
            if draft_version_no is not None:  # BR-2 authoring test path
                version = await uow.versions.get(model.id, draft_version_no)
                if version is None:
                    raise NotFound("version not found")
                if version.status not in ("draft", "in_review", "rejected"):
                    raise Conflict("X-Draft-Version must reference an open version")
            else:
                if not model.published_version_id:
                    raise ModelNotPublished(
                        f"model {model.name!r} has no published version")
                version = await uow.versions.get_by_id(model.published_version_id)
                if version is None:
                    raise NotFound("published version not found")

        broken = _broken_names(model)
        version_label = f"{model.name}@v{version.version_no}"
        cache_key = (model.id, version.id, req.request_hash(), dialect,
                     sha256_hex(",".join(sorted(broken))))
        cached = self._cache.get(cache_key) if draft_version_no is None else None
        if cached is not None:
            result = dict(cached)
        else:
            compiler = Compiler(
                parse_definition(version.definition, settings=self.settings),
                model_version_label=version_label,
                broken_names=broken, settings=self.settings,
                now=self.clock.now(), timezone=self.settings.reporting_timezone,
            )
            compiled: Compiled = compiler.compile(req, dialect)
            provenance = {
                "model_version": version_label,
                "measures": compiled.measures,
                "dimensions": compiled.dimensions,
            }
            if compiled.join_paths:
                provenance["join_paths"] = compiled.join_paths
            if compiled.time_range_resolved:
                provenance["time_range"] = compiled.time_range_resolved
            result = {
                "sql": compiled.sql,
                "params": compiled.params,
                "engine_dialect": dialect,
                "output_schema": compiled.output_schema,
                "provenance": provenance,
                "warnings": compiled.warnings,
            }
            if draft_version_no is None:
                self._cache[cache_key] = dict(result)

        result["warnings"] = list(result["warnings"]) + warnings_extra
        if validate and token:  # SEM-FR-024
            result["validation"] = await self.deps.query_client.dry_run(
                ctx.tenant_id, result["sql"], result["params"], dialect, token)
        elif validate:
            # dry-run is forwarded under the caller's own JWT (query-service has
            # no internal/SPIFFE route); degrade rather than block the compiled
            # SQL when no caller token reached this call.
            result["validation"] = {"valid": None, "estimated_bytes": None,
                                    "verdict": "unavailable",
                                    "message": "no caller token available for dry-run"}

        duration_ms = int((time.monotonic() - started) * 1000)
        async with self.uow(ctx.tenant_id) as uow:  # compile_log (§4.1)
            await uow.compile_log.add(CompileLogEntry(
                id=str(uuid7()), tenant_id=ctx.tenant_id,
                model_version_id=version.id, request_hash=req.request_hash(),
                request=req.canonical(), caller_class=caller_class, dialect=dialect,
                warnings=result["warnings"], duration_ms=duration_ms,
                created_at=self.clock.now(),
            ))
            await uow.commit()
        return result

    async def compile_chart(self, ctx: CallCtx, body: dict, *,
                            caller_class: str = "chart",
                            validate: bool = False, token: str | None = None) -> dict:
        mapped = map_chart_request(body)
        if mapped.get("passthrough"):  # BR-13
            return {"passthrough": True, "reason": mapped.get("reason"),
                    "sql": None, "params": [], "output_schema": []}
        mapped["model"] = body.get("model")
        mapped["workspace_id"] = body.get("workspace_id")
        mapped["dialect"] = body.get("dialect") or "trino"
        result = await self.compile(ctx, mapped, caller_class=caller_class,
                                    validate=validate, token=token)
        result["passthrough"] = False
        return result


# ---------------------------------------------------------------------------


class VerifiedQueryService(_Base):
    async def create(self, ctx: CallCtx, body: dict,
                     provenance: dict | None = None) -> VerifiedQuery:
        validate_read_only_sql(body["sql_text"])  # BR-11 / AC-11
        for var in body.get("variables") or []:
            if not isinstance(var, dict) or not var.get("name") or not var.get("type"):
                raise ValidationFailed("variables must be [{name, type, required?}]")
        now = self.clock.now()
        embedding = await self.deps.embeddings.embed(ctx.tenant_id, body["nl_text"])
        async with self.uow(ctx.tenant_id) as uow:
            model_id = None
            if body.get("model"):
                model = await self._resolve_model(ctx, uow, body["model"],
                                                  body.get("workspace_id"))
                model_id = model.id
            vq = VerifiedQuery(
                id=str(uuid7()), tenant_id=ctx.tenant_id,
                workspace_id=body["workspace_id"], model_id=model_id,
                nl_text=body["nl_text"], sql_text=body["sql_text"],
                variables=body.get("variables") or [], status="draft",
                tags=body.get("tags") or [], provenance=provenance,
                health_note=None, embedding=embedding,
                submitted_by=ctx.subject or "", approved_by=None, decided_at=None,
                created_at=now, updated_at=now,
            )
            await uow.verified_queries.add(vq)
            await uow.commit()
            return vq

    async def _get(self, ctx: CallCtx, uow, vq_id: str) -> VerifiedQuery:
        vq = await uow.verified_queries.get(vq_id)
        if vq is None:
            await self._audit_cross_tenant(
                ctx, verified_query_urn(ctx.tenant_id, vq_id),
                "verified query not visible in tenant")
            raise NotFound("verified query not found")
        return vq

    async def get(self, ctx: CallCtx, vq_id: str) -> VerifiedQuery:
        async with self.uow(ctx.tenant_id) as uow:
            return await self._get(ctx, uow, vq_id)

    async def list(self, ctx: CallCtx, workspace_id: str | None, status: str | None,
                   limit: int, cursor: str | None) -> Page:
        async with self.uow(ctx.tenant_id) as uow:
            return await uow.verified_queries.list(workspace_id, status, limit, cursor)

    async def patch(self, ctx: CallCtx, vq_id: str, patch: dict) -> VerifiedQuery:
        async with self.uow(ctx.tenant_id) as uow:
            vq = await self._get(ctx, uow, vq_id)
            if vq.status not in ("draft", "rejected"):
                raise Conflict(f"verified query is {vq.status}; only drafts are editable")
            if vq.status == "rejected":
                check_vq_transition("rejected", "draft")
                vq.status = "draft"
            if "sql_text" in patch:
                validate_read_only_sql(patch["sql_text"])
                vq.sql_text = patch["sql_text"]
            if "nl_text" in patch and patch["nl_text"] != vq.nl_text:
                vq.nl_text = patch["nl_text"]
                vq.embedding = await self.deps.embeddings.embed(ctx.tenant_id, vq.nl_text)
            if "variables" in patch:
                vq.variables = patch["variables"]
            if "tags" in patch:
                vq.tags = patch["tags"]
            vq.updated_at = self.clock.now()
            await uow.verified_queries.update(vq)
            await uow.commit()
            return vq

    async def _transition(self, ctx: CallCtx, vq_id: str, target: str,
                          event: str, *, require_other_actor: bool = False,
                          note: str | None = None) -> VerifiedQuery:
        async with self.uow(ctx.tenant_id) as uow:
            vq = await self._get(ctx, uow, vq_id)
            check_vq_transition(vq.status, target)
            if require_other_actor and vq.submitted_by == ctx.subject:
                raise PermissionDenied(
                    "author cannot decide their own verified query (SEM-FR-040)")
            if target == "pending_review":
                validate_read_only_sql(vq.sql_text)  # re-run at submit
            vq.status = target
            if target in ("approved", "rejected"):
                vq.approved_by = ctx.subject
                vq.decided_at = self.clock.now()
                validate_read_only_sql(vq.sql_text)  # approval re-runs validation (BR-11)
            vq.updated_at = self.clock.now()
            await uow.verified_queries.update(vq)
            payload = {"status": target}
            if note:
                payload["note"] = note
            await self._emit(uow, ctx, event, verified_query_urn(ctx.tenant_id, vq.id),
                             payload)
            await uow.commit()
            return vq

    async def submit(self, ctx: CallCtx, vq_id: str) -> VerifiedQuery:
        return await self._transition(ctx, vq_id, "pending_review",
                                      "verified_query.submitted")

    async def approve(self, ctx: CallCtx, vq_id: str) -> VerifiedQuery:
        return await self._transition(ctx, vq_id, "approved", "verified_query.approved",
                                      require_other_actor=True)

    async def reject(self, ctx: CallCtx, vq_id: str, note: str | None) -> VerifiedQuery:
        return await self._transition(ctx, vq_id, "rejected", "verified_query.rejected",
                                      require_other_actor=True, note=note)

    async def archive(self, ctx: CallCtx, vq_id: str) -> VerifiedQuery:
        return await self._transition(ctx, vq_id, "archived", "verified_query.archived")

    async def search(self, ctx: CallCtx, workspace_id: str, q: str,
                     top_k: int) -> list[dict]:
        """SEM-FR-041: ANN over approved pairs, hard tenant+workspace filter (BR-14)."""
        if not q:
            raise ValidationFailed("q is required")
        top_k = max(1, min(top_k, self.settings.search_top_k_max))
        embedding = await self.deps.embeddings.embed(ctx.tenant_id, q)
        async with self.uow(ctx.tenant_id) as uow:
            hits = await uow.verified_queries.search(workspace_id, embedding, top_k)
        return [
            {"id": vq.id, "nl_text": vq.nl_text, "sql_text": vq.sql_text,
             "variables": vq.variables, "tags": vq.tags, "model_id": vq.model_id,
             "score": round(score, 6)}
            for vq, score in hits
        ]


# ---------------------------------------------------------------------------


class BootstrapService(_Base):
    """SEM-FR-060..062: derive draft definitions from V1 chart configs +
    saved queries supplied as artifacts. 202 + operation record (MASTER-FR-027);
    derivation is synchronous (artifacts are inline)."""

    async def run(self, ctx: CallCtx, model_id: str, sources: dict) -> Operation:
        chart_configs = sources.get("chart_configs") or []
        saved_queries = sources.get("saved_queries") or []
        now = self.clock.now()

        async with self.uow(ctx.tenant_id) as uow:
            await self._get_model(ctx, uow, model_id)
            version = await uow.versions.open_version(model_id)
            if version is not None and version.status == "in_review":
                raise Conflict("open version is in_review; bootstrap needs a draft")

        if version is None:
            version_service = VersionService(self.deps)
            version = await version_service.create_draft(ctx, model_id)

        # Pre-fetch dataset bindings (deriver uses a sync lookup)
        urns = {c.get("dataset_urn") for c in chart_configs if c.get("dataset_urn")}
        urns |= {q.get("dataset_urn") for q in saved_queries if q.get("dataset_urn")}
        lookups = {
            urn: await self.deps.dataset_client.get_dataset(ctx.tenant_id, urn)
            for urn in urns
        }

        deriver = BootstrapDeriver(version.definition, lambda urn: lookups.get(urn))
        for chart in chart_configs:
            deriver.add_chart(chart)
        for query in saved_queries:
            deriver.add_saved_query(query)
        new_definition = deriver.defn
        parse_definition(new_definition, settings=self.settings)  # never save junk
        report = deriver.report()

        operation = Operation(
            id=str(uuid7()), tenant_id=ctx.tenant_id, kind="bootstrap",
            status="completed", resource_urn=model_urn(ctx.tenant_id, model_id),
            report={"operation_id": None, "status": "completed", **report},
            created_at=now, finished_at=self.clock.now(),
        )
        operation.report["operation_id"] = operation.id

        async with self.uow(ctx.tenant_id) as uow:
            fresh = await uow.versions.get(model_id, version.version_no)
            if fresh is None or fresh.status != "draft":
                raise Conflict("draft version changed during bootstrap; retry")
            fresh.definition = new_definition
            await uow.versions.update(fresh)
            await uow.operations.add(operation)
            await self._emit(uow, ctx, "bootstrap.completed",
                             model_urn(ctx.tenant_id, model_id),
                             {"created_counts": report["created"],
                              "operation_id": operation.id})
            await uow.commit()
        return operation

    async def get_operation(self, ctx: CallCtx, op_id: str) -> Operation:
        async with self.uow(ctx.tenant_id) as uow:
            op = await uow.operations.get(op_id)
            if op is None:
                raise NotFound("operation not found")
            return op

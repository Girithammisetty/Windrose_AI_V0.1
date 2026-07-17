"""Dataset CRUD, versions, profiles, similarity, consumers (BRD §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import (
    DatasetCreate,
    DatasetPatch,
    SimilarRequest,
    dataset_payload,
    page_envelope,
    resolve_payload,
    version_payload,
)
from app.domain.errors import ValidationFailed
from app.domain.ports import DatasetFilters
from app.domain.urn import parse_urn, parse_version_urn

router = APIRouter(prefix="/api/v1")

_SORTS = {"-created_at", "created_at", "name", "-name", "row_count", "-row_count"}


def _svc(request: Request):
    return request.app.state.container


@router.post("/datasets", status_code=201)
async def create_dataset(
    request: Request,
    response: Response,
    body: DatasetCreate,
    principal: Principal = Depends(require("dataset.dataset.create")),
):
    c = _svc(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        dataset = await c.dataset_service.create(ctx, body.model_dump())
        return 201, {"data": dataset_payload(dataset)}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/datasets")
async def list_datasets(
    request: Request,
    principal: Principal = Depends(require("dataset.dataset.read")),
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    sort: str = "-created_at",
    status: str | None = Query(default=None, alias="filter[status]"),
    tags: str | None = Query(default=None, alias="filter[tags]"),
    created_by: str | None = Query(default=None, alias="filter[created_by]"),
    column: str | None = Query(default=None, alias="filter[column]"),
    quality_flag: str | None = Query(default=None, alias="filter[quality_flag]"),
    has_pii: bool | None = Query(default=None, alias="filter[has_pii]"),
):
    c = _svc(request)
    sort = sort if sort in _SORTS else "-created_at"
    filters = DatasetFilters(
        q=q,
        status=status,
        tags=tags.split(",") if tags else [],
        created_by=created_by,
        column=column,
        quality_flag=quality_flag,
        has_pii=has_pii,
    )
    page = await c.dataset_service.list(
        principal.ctx(request.state.trace_id), filters, sort, limit, cursor
    )
    return page_envelope(
        [dataset_payload(ds) for ds in page.items], page.next_cursor, page.has_more
    )


# INTERNAL, UNAUTHENTICATED resolver for query-service (QRY-FR-005).
#
# query-service's dataset resolver calls GET /api/v1/datasets/resolve with NO
# Authorization header (it is a mesh-internal service-to-service call), so this
# route is intentionally NOT behind require(<action>) — a JWT/authz guard would
# 401 the caller. It is also exempted from AuthMiddleware (see
# app/api/middleware.py). It returns only physical-location metadata
# (bucket/keys/columns); the actual row data stays tenant-RLS-guarded when
# query-service executes the SQL under the end user. The tenant is supplied by
# the trusted caller via the `tenant` query param and threaded into the UoW so
# the RLS `app.tenant_id` GUC scopes the dataset lookup.
#
# Declared BEFORE /datasets/{dataset_id} so "resolve" is not captured as a path
# param.
@router.get("/datasets/resolve")
async def resolve_dataset(
    request: Request,
    tenant: str | None = None,
    name: str | None = None,
    version: int = 0,
):
    if not tenant:
        raise ValidationFailed("tenant query param is required")
    if not name:
        raise ValidationFailed("name query param is required")
    c = _svc(request)
    dataset, dsv, source_uris, columns = await c.dataset_service.resolve(
        tenant, name, version
    )
    return resolve_payload(dataset, dsv, source_uris, columns)


@router.get("/artifacts")
async def get_artifact(
    request: Request,
    urn: str = Query(...),
    principal: Principal = Depends(require("dataset.profile.read")),
):
    """Resolve a dataset (version) URN to its "metric artifact" — the dataset's
    profile summary rendered as key/value metrics (CHART-FR-025). chart-service's
    metric/parameter family GETs this (`FetchArtifact`) and passes the
    ``{kind, metrics}`` blob through to the chart UI. Guarded by the same
    permission as GET /datasets/{id}/profile. Accepts both the dataset version
    URN (``wr:<t>:dataset:version/<id>@v<no>``) and the plain dataset URN
    (``wr:<t>:dataset:dataset/<id>`` — current-version)."""
    parsed = parse_urn(urn)
    if parsed.service != "dataset":
        raise ValidationFailed(f"not a dataset URN: {urn!r}")
    ver = parse_version_urn(parsed)
    if ver is not None:
        dataset_id, version_no = ver
    elif parsed.rtype == "dataset":
        dataset_id, version_no = parsed.rid, None
    else:
        raise ValidationFailed(f"unsupported dataset URN form: {urn!r}")
    c = _svc(request)
    artifact = await c.profile_service.metric_artifact(
        principal.ctx(request.state.trace_id), dataset_id, version_no
    )
    return {"data": artifact}


@router.get("/datasets/{dataset_id}")
async def get_dataset(
    request: Request,
    response: Response,
    dataset_id: str,
    principal: Principal = Depends(require("dataset.dataset.read")),
):
    c = _svc(request)
    dataset, current = await c.dataset_service.get(
        principal.ctx(request.state.trace_id), dataset_id
    )
    payload = dataset_payload(dataset, current)
    response.headers["ETag"] = f'"{payload["etag"]}"'
    return {"data": payload}


@router.patch("/datasets/{dataset_id}")
async def patch_dataset(
    request: Request,
    response: Response,
    dataset_id: str,
    body: DatasetPatch,
    principal: Principal = Depends(require("dataset.dataset.update")),
):
    c = _svc(request)
    dataset = await c.dataset_service.patch(
        principal.ctx(request.state.trace_id),
        dataset_id,
        body.model_dump(exclude_unset=True),
        request.headers.get("if-match"),
    )
    payload = dataset_payload(dataset)
    response.headers["ETag"] = f'"{payload["etag"]}"'
    return {"data": payload}


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    request: Request,
    dataset_id: str,
    force: bool = False,
    principal: Principal = Depends(require("dataset.dataset.delete")),
):
    c = _svc(request)
    summary = await c.dataset_service.delete(
        principal.ctx(request.state.trace_id), dataset_id, force
    )
    return {"data": {"id": dataset_id, "deleted": True, "consumers": summary}}


@router.post("/datasets/{dataset_id}/restore")
async def restore_dataset(
    request: Request,
    dataset_id: str,
    principal: Principal = Depends(require("dataset.dataset.update")),
):
    c = _svc(request)
    dataset = await c.dataset_service.restore(
        principal.ctx(request.state.trace_id), dataset_id
    )
    return {"data": dataset_payload(dataset)}


@router.get("/datasets/{dataset_id}/rows")
async def browse_dataset_rows(
    request: Request,
    dataset_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    sort: str | None = Query(default=None),
    dir: str = Query(default="asc"),
    principal: Principal = Depends(require("dataset.dataset.read")),
):
    """Paginated, filterable, sortable browse of a dataset's current-version
    rows (DST-FR-050). Per-column filters arrive as repeated query params
    ``filter=<col>:<op>:<value>`` where op ∈ eq|neq|contains|gt|gte|lt|lte.
    Returns {columns, rows, total, filtered, offset, limit}."""
    filters: list[dict] = []
    for raw in request.query_params.getlist("filter"):
        # col may itself contain ':' only in pathological cases; split into 3.
        parts = raw.split(":", 2)
        if len(parts) == 3 and parts[0]:
            filters.append({"col": parts[0], "op": parts[1], "value": parts[2]})
        elif len(parts) == 2 and parts[0]:
            filters.append({"col": parts[0], "op": "contains", "value": parts[1]})
    c = _svc(request)
    result = await c.dataset_service.browse_rows(
        principal.ctx(request.state.trace_id), dataset_id,
        offset=offset, limit=limit, sort_col=sort,
        sort_dir=("desc" if dir == "desc" else "asc"), filters=filters,
    )
    return {"data": result}


@router.get("/datasets/{dataset_id}/consumers")
async def dataset_consumers(
    request: Request,
    dataset_id: str,
    principal: Principal = Depends(require("dataset.dataset.read")),
):
    c = _svc(request)
    summary = await c.dataset_service.consumers_summary(
        principal.ctx(request.state.trace_id), dataset_id
    )
    return {"data": summary}


# `POST /datasets:similar` — colon paths clash with path templates, so the
# canonical route is exposed verbatim via a raw path plus an alias.
@router.post("/datasets:similar")
async def similar_datasets(
    request: Request,
    body: SimilarRequest,
    principal: Principal = Depends(require("dataset.dataset.read")),
):
    c = _svc(request)
    ranked = await c.dataset_service.similar(
        principal.ctx(request.state.trace_id),
        schema=body.schema_,
        columns=body.columns,
    )
    return {"data": ranked}


@router.get("/datasets/{dataset_id}/versions")
async def list_versions(
    request: Request,
    dataset_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    principal: Principal = Depends(require("dataset.dataset.read")),
):
    c = _svc(request)
    page = await c.version_service.list(
        principal.ctx(request.state.trace_id), dataset_id, limit, cursor
    )
    return page_envelope(
        [version_payload(v) for v in page.items], page.next_cursor, page.has_more
    )


@router.get("/datasets/{dataset_id}/versions/{version_no}")
async def get_version(
    request: Request,
    dataset_id: str,
    version_no: int,
    principal: Principal = Depends(require("dataset.dataset.read")),
):
    c = _svc(request)
    version = await c.version_service.get(
        principal.ctx(request.state.trace_id), dataset_id, version_no
    )
    return {"data": version_payload(version)}


@router.post("/datasets/{dataset_id}/versions/{version_no}/profile", status_code=202)
async def trigger_profile(
    request: Request,
    response: Response,
    dataset_id: str,
    version_no: int,
    principal: Principal = Depends(require("dataset.profile.execute")),
):
    c = _svc(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        profile = await c.profile_service.trigger(ctx, dataset_id, version_no)
        return 202, {"data": {"operation_id": profile.id, "profile_id": profile.id,
                              "status": str(profile.status)}}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/datasets/{dataset_id}/profile")
async def get_profile(
    request: Request,
    dataset_id: str,
    version: int | None = None,
    principal: Principal = Depends(require("dataset.profile.read")),
):
    c = _svc(request)
    summary = await c.profile_service.get_summary(
        principal.ctx(request.state.trace_id), dataset_id, version
    )
    return {"data": summary}

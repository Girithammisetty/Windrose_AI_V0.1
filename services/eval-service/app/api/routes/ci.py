"""CI gate plane (EVL-FR-021a/030). CI posts a candidate build; the suite runs and
the gate verdict is produced vs baseline. Accepts an allowed SPIFFE identity (mTLS)
or a service JWT."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.api.schemas import CiEvaluate
from app.domain.entities import CallCtx
from app.domain.errors import Unauthenticated

router = APIRouter(prefix="/api/v1")


def _ci_ctx(request: Request, tenant_hint: str | None) -> CallCtx:
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return principal.ctx(getattr(request.state, "trace_id", None))
    spiffe = getattr(request.state, "spiffe", None)
    if spiffe:
        tenant = tenant_hint or request.app.state.settings.register_tenant_id
        if not tenant:
            raise Unauthenticated("CI call requires tenant_id (body) when authenticated via SPIFFE")
        return CallCtx(
            tenant_id=tenant,
            actor={"type": "service", "id": spiffe},
            trace_id=getattr(request.state, "trace_id", None),
        )
    raise Unauthenticated("missing credentials")


@router.post("/ci/evaluate", status_code=202)
async def ci_evaluate(request: Request, body: CiEvaluate):
    container = request.app.state.container
    tenant_hint = getattr(body, "tenant_id", None)
    ctx = _ci_ctx(request, tenant_hint)

    # BR-10 idempotency / AC-7 reuse: an existing gate for this exact content digest
    # + suite/dataset pins is returned without a duplicate run.
    existing = await container.gate_service.find_by_digest(ctx, body.agent_key, body.build_digest)
    suite = await container.suite_service.get(ctx, body.suite_id, body.suite_version)
    dataset_version = suite.datasets[0]["version"]
    for g in existing:
        if (
            g.suite_id == suite.suite_id
            and g.suite_version == suite.version
            and g.dataset_version == dataset_version
        ):
            return JSONResponse(
                {
                    "data": {
                        "operation_id": g.run_id,
                        "gate_run_id": g.gate_run_id,
                        "gate_passed": g.gate_passed,
                        "reused": True,
                    }
                },
                status_code=202,
            )

    provider = container.candidate_provider(body.candidate_outputs)
    candidate = {"content_digest": body.build_digest, "commit": body.commit, "repo": body.repo}
    run = await container.run_service.create_and_execute(
        ctx,
        trigger="ci",
        agent_key=body.agent_key,
        candidate=candidate,
        suite_id=body.suite_id,
        suite_version=body.suite_version,
        candidate_provider=provider,
        baseline=body.baseline,
    )
    gate = await container.gate_service.evaluate_from_run(ctx, run.id)
    return JSONResponse(
        {
            "data": {
                "operation_id": run.id,
                "gate_run_id": gate.gate_run_id,
                "gate_passed": gate.gate_passed,
                "reused": False,
            }
        },
        status_code=202,
    )

"""Reserved online-serving namespace (INF-FR-070, AC-15).

KServe online serving is a later phase; the namespace and the
``serving_endpoints`` table are reserved. Every route here returns a stable
``501 NOT_IMPLEMENTED`` error body.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.domain.errors import NotImplementedYet

router = APIRouter(prefix="/api/v1")


async def _reserved(_request: Request):
    raise NotImplementedYet(
        "online serving (KServe) is not implemented in this phase; namespace reserved"
    )


router.add_api_route("/endpoints", _reserved,
                     methods=["GET", "POST"], status_code=501)
router.add_api_route("/endpoints/{endpoint_id}", _reserved,
                     methods=["GET", "DELETE"], status_code=501)
router.add_api_route("/endpoints/{endpoint_id}/predict", _reserved,
                     methods=["POST"], status_code=501)

"""JWKS endpoint (ART-FR §5 / tool-plane TPL-FR-035).

tool-plane fetches our PUBLIC signing key here (PROPOSAL_JWKS_URL defaults to
``.../api/v1/.well-known/jwks.json``) to verify our proposal-execution grants and
A2A card signatures. Served at both the versioned and root well-known paths.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


def _jwks(request: Request) -> dict:
    return request.app.state.container.signing_key.jwks()


@router.get("/api/v1/.well-known/jwks.json")
async def jwks_versioned(request: Request):
    return _jwks(request)


@router.get("/.well-known/jwks.json")
async def jwks_root(request: Request):
    return _jwks(request)


@router.get("/.well-known/agent-runtime-jwks.json")
async def jwks_named(request: Request):
    return _jwks(request)

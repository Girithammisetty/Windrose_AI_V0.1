"""A2A signed agent cards (ART-FR-050). Cards are served per published version and
carry an RS256 signature verifiable against our JWKS."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.domain.errors import NotFound

router = APIRouter(prefix="/api/v1")


@router.get("/a2a/cards/{agent_key}")
async def get_card(request: Request, agent_key: str):
    c = request.app.state.container
    v = await c.store.latest_published_version(agent_key)
    if v is None:
        raise NotFound(f"agent {agent_key} has no published version")
    version = await c.store.get_agent_version(agent_key, v)
    return {"data": version.a2a_card}

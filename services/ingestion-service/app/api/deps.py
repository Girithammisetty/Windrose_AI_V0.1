"""FastAPI dependencies: container access, principal extraction, authz helper."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.api.auth import JWKSKeyProvider, Principal, verify_token_async
from app.container import Container
from app.domain.errors import UnauthenticatedError


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_principal(
    container: ContainerDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthenticatedError("missing bearer token")
    token = authorization.split(" ", 1)[1]
    jwks = container.jwks if isinstance(container.jwks, JWKSKeyProvider) else None
    return await verify_token_async(token, container.settings, jwks)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def tenant_urn(tenant_id: str, resource_type: str, resource_id: str) -> str:
    """MASTER-FR-013 resource URNs: wr:<tenant>:<service>:<type>/<id>."""
    return f"wr:{tenant_id}:ingestion:{resource_type}/{resource_id}"

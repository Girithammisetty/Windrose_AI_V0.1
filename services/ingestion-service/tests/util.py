"""Shared test utilities."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import sqlalchemy as sa

from app.container import Container
from app.store.models import OutboxEvent

TENANT_A = "11111111-1111-7111-8111-111111111111"
TENANT_B = "22222222-2222-7222-8222-222222222222"
ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"


def make_token(
    private_pem: bytes,
    tenant_id: str = TENANT_A,
    sub: str = "user-1",
    typ: str = "user",
    scopes: list[str] | None = None,
    **extra: Any,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": sub,
        "tenant_id": tenant_id,
        "typ": typ,
        "scopes": scopes or ["ingestion.*"],
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **extra,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


VALID_PG_CONNECTION = {
    "name": "Prod Warehouse",
    "connector_type": "postgres",
    "config": {"host": "db.acme.internal", "port": 5432, "database": "sales", "username": "ro"},
    "secrets": {"password": "s3cr3t-pw"},
}


async def create_connection(client, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    payload = {**VALID_PG_CONNECTION, **overrides}
    resp = await client.post("/api/v1/connections", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def csv_blob(rows: int, *, bad_rows: int = 0, header: str = "id,name,score") -> bytes:
    """Deterministic CSV; bad rows have a wrong column count."""
    lines = [header]
    for i in range(rows):
        lines.append(f"{i},name-{i},{i % 100}")
    for i in range(bad_rows):
        lines.append(f"bad-{i},only-two-cols")
    return ("\n".join(lines) + "\n").encode()


def slice_parts(blob: bytes, part_size: int) -> list[bytes]:
    return [blob[i : i + part_size] for i in range(0, len(blob), part_size)]


async def upload_file_flow(
    client,
    headers: dict[str, str],
    content: bytes,
    *,
    part_size: int = 1024,
    file_format: str = "csv",
    sha256: str | None = None,
    allow_empty: bool = False,
    error_row_limit: int = 100,
) -> dict[str, Any]:
    """Full file-upload flow: create job -> init upload -> PUT parts -> complete."""
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "file_upload",
            "file_format": file_format,
            "new_dataset": {"name": "upload-test"},
            "allow_empty": allow_empty,
            "error_row_limit": error_row_limit,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    ingestion = resp.json()["data"]

    resp = await client.post(
        "/api/v1/uploads",
        json={"ingestion_id": ingestion["id"], "part_size": part_size, "bytes_total": len(content)},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    upload = resp.json()["data"]

    parts = slice_parts(content, part_size)
    manifest = []
    for n, part in enumerate(parts, start=1):
        resp = await client.put(
            f"/api/v1/uploads/{upload['upload_id']}/parts/{n}", content=part, headers=headers
        )
        assert resp.status_code == 200, resp.text
        manifest.append(resp.json()["data"])

    body: dict[str, Any] = {"parts": manifest}
    if sha256:
        body["sha256"] = sha256
    resp = await client.post(
        f"/api/v1/uploads/{upload['upload_id']}/complete", json=body, headers=headers
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]


async def outbox_events(
    container: Container, tenant_id: str, event_type: str | None = None
) -> list[dict[str, Any]]:
    async with container.db.tenant_session(tenant_id) as session:
        stmt = sa.select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id)
        if event_type:
            stmt = stmt.where(OutboxEvent.event_type == event_type)
        stmt = stmt.order_by(OutboxEvent.occurred_at, OutboxEvent.id)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {"event_type": r.event_type, "payload": r.payload, "resource_urn": r.resource_urn}
            for r in rows
        ]


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)

"""Vendored platform contracts: idempotency, outbox, pagination codec,
authz matrix, unit-tier tenant isolation (MASTER-FR-022/025/034/071)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.api.pagination import clamp_limit, decode_cursor, encode_cursor
from app.domain.errors import ValidationFailedError
from app.events.outbox import publish_pending
from app.ids import uuid7
from app.store.models import IdempotencyKey, OutboxEvent
from tests.util import TENANT_A, TENANT_B, VALID_PG_CONNECTION, create_connection, outbox_events


async def test_idempotency_key_replays_original_response(client, auth_a, container) -> None:
    headers = {**auth_a, "Idempotency-Key": "idem-123"}
    first = await client.post("/api/v1/connections", json=VALID_PG_CONNECTION, headers=headers)
    assert first.status_code == 201
    assert "Idempotency-Replayed" not in first.headers
    second = await client.post("/api/v1/connections", json=VALID_PG_CONNECTION, headers=headers)
    assert second.status_code == 201
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json() == first.json()
    listing = await client.get("/api/v1/connections", headers=auth_a)
    assert len(listing.json()["data"]) == 1  # one side effect only


async def test_idempotency_key_with_different_payload_conflicts(client, auth_a) -> None:
    headers = {**auth_a, "Idempotency-Key": "idem-456"}
    first = await client.post("/api/v1/connections", json=VALID_PG_CONNECTION, headers=headers)
    assert first.status_code == 201
    other = {**VALID_PG_CONNECTION, "name": "Other"}
    second = await client.post("/api/v1/connections", json=other, headers=headers)
    assert second.status_code == 409


async def test_failed_handler_releases_idempotency_claim(client, auth_a, container) -> None:
    headers = {**auth_a, "Idempotency-Key": "idem-789"}
    bad = {
        **VALID_PG_CONNECTION,
        "config": {**VALID_PG_CONNECTION["config"], "host": "unreachable.internal"},
    }
    resp = await client.post("/api/v1/connections", json=bad, headers=headers)
    assert resp.status_code == 424
    async with container.db.tenant_session(TENANT_A) as session:
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(IdempotencyKey))
        ).scalar_one()
    assert count == 0
    # a retry with a fixed payload can reuse the key
    resp = await client.post("/api/v1/connections", json=VALID_PG_CONNECTION, headers=headers)
    assert resp.status_code == 201


async def test_outbox_publisher_marks_rows_published(client, auth_a, container) -> None:
    await create_connection(client, auth_a)
    async with container.db.tenant_session(TENANT_A) as session:
        published = await publish_pending(session, container.publisher)
        assert published >= 1
        remaining = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
            )
        ).scalar_one()
    assert remaining == 0
    topics = {t for t, _k, _v in container.publisher.published}
    assert topics == {"ingestion.events.v1"}
    envelope = container.publisher.published[0][2]
    assert {"event_id", "event_type", "tenant_id", "actor", "resource_urn", "occurred_at"} <= set(
        envelope
    )


def test_cursor_codec_roundtrip_and_validation() -> None:
    resource_id = uuid7()
    assert decode_cursor(encode_cursor(resource_id)) == resource_id
    with pytest.raises(ValidationFailedError):
        decode_cursor("@@not-base64@@")
    with pytest.raises(ValidationFailedError):
        decode_cursor(encode_cursor("not-a-uuid"))
    assert clamp_limit(None) == 50
    with pytest.raises(ValidationFailedError):
        clamp_limit(201)
    with pytest.raises(ValidationFailedError):
        clamp_limit(0)


async def test_unit_tier_tenant_isolation_with_policy_fake(
    client, auth_a, auth_b, container
) -> None:
    """CONVENTIONS testing tier: unit-tier isolation via explicit tenant filters."""
    created = await create_connection(client, auth_a)
    resp = await client.get(f"/api/v1/connections/{created['id']}", headers=auth_b)
    assert resp.status_code == 404  # MASTER-FR-003: 404, not 403
    listing = await client.get("/api/v1/connections", headers=auth_b)
    assert listing.json()["data"] == []
    denied = await outbox_events(container, TENANT_B, "security.cross_tenant_denied")
    assert len(denied) == 1


AUTHZ_CASES = [
    ("ingestion.connection.create", "POST", "/api/v1/connections", VALID_PG_CONNECTION),
    ("ingestion.connection.read", "GET", "/api/v1/connections", None),
    (
        "ingestion.ingestion.create",
        "POST",
        "/api/v1/ingestions",
        {"ingestion_mode": "file_upload", "file_format": "csv", "new_dataset": {"name": "x"}},
    ),
    ("ingestion.ingestion.read", "GET", "/api/v1/ingestions", None),
    ("ingestion.upload.create", "POST", "/api/v1/uploads", {"ingestion_id": uuid7()}),
    ("ingestion.schedule.read", "GET", "/api/v1/schedules", None),
]


@pytest.mark.parametrize(("action", "method", "path", "body"), AUTHZ_CASES)
async def test_authz_matrix(client, auth_a, container, action, method, path, body) -> None:
    """MASTER-FR-071: every endpoint enforces its action; deny -> 403."""
    container.policy.deny("user-a", action)
    resp = await client.request(method, path, json=body, headers=auth_a)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"
    container.policy.denied.clear()
    resp = await client.request(method, path, json=body, headers=auth_a)
    assert resp.status_code not in (401, 403)

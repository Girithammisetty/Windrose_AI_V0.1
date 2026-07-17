"""Postgres persistence: migrations apply, full API flows round-trip through
jsonb/uuid/partitioned tables (MASTER-FR-060..062)."""

from __future__ import annotations

import sqlalchemy as sa

from app.domain.querysource import FakeQuerySource
from tests.util import create_connection, csv_blob, upload_file_flow

ROWS = [{"id": i, "name": f"n{i}"} for i in range(5)]


async def test_connection_crud_roundtrip(pg_client, auth_a) -> None:
    created = await create_connection(pg_client, auth_a)
    assert created["last_test_status"] == "ok"

    got = await pg_client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert got.status_code == 200
    assert got.json()["data"]["config"]["host"] == "db.acme.internal"
    assert got.json()["data"]["secrets"] == {"password": "•••"}

    patched = await pg_client.patch(
        f"/api/v1/connections/{created['id']}", json={"tags": ["gold"]}, headers=auth_a
    )
    assert patched.json()["data"]["tags"] == ["gold"]

    dup = await pg_client.post(
        "/api/v1/connections",
        json={
            "name": created["name"].upper(),
            "connector_type": "postgres",
            "config": {"host": "h", "database": "d", "username": "u"},
        },
        headers=auth_a,
    )
    assert dup.status_code == 409  # functional unique index (lower(name))

    deleted = await pg_client.delete(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert deleted.status_code == 204


async def test_query_ingestion_persists_into_partitioned_table(
    pg_client, auth_a, pg_app_container
) -> None:
    pg_app_container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(pg_client, auth_a)
    resp = await pg_client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM t",
            "new_dataset": {"name": "pg-orders"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()["data"]
    assert job["status"] == "completed"
    assert job["rows_appended"] == 5

    got = await pg_client.get(f"/api/v1/ingestions/{job['id']}", headers=auth_a)
    assert got.json()["data"]["iceberg_snapshot_id"] == 1

    progress = await pg_client.get(f"/api/v1/ingestions/{job['id']}/progress", headers=auth_a)
    assert progress.json()["data"]["snapshot"]["status"] == "completed"


async def test_upload_flow_survives_on_postgres(pg_client, auth_a) -> None:
    """Part state in Postgres + bytes in the object store (ING-FR-042)."""
    job = await upload_file_flow(pg_client, auth_a, csv_blob(120), part_size=512)
    assert job["status"] == "completed"
    assert job["rows_appended"] == 120


async def test_transitions_recorded_in_partitioned_history(pg_client, auth_a, su_engine) -> None:
    job = await upload_file_flow(pg_client, auth_a, csv_blob(30), part_size=512)
    with su_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT from_status, to_status FROM ingestion_transitions "
                "WHERE ingestion_id = :id ORDER BY created_at, id"
            ),
            {"id": job["id"]},
        ).fetchall()
    path = [tuple(r) for r in rows]
    assert path[0] == ("created", "awaiting_upload")
    assert path[-1] == ("committing", "completed")

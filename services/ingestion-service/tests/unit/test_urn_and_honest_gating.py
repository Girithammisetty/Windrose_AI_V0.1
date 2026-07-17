"""URN single-source-of-truth + honest gating of unimplemented surfaces.

- ingestion.completed must carry the minted dataset_id (== the id inside
  dataset_urn) so dataset-service's consumer registers the dataset row under
  the SAME id every other consumer of the URN resolves (the URN-drift fix).
- The real container's registry defaults reject unwired connector types
  (UNSUPPORTED_CONNECTOR) instead of faking a successful probe / silent
  zero-row ingest.
- webhook_batch ingestion creation is rejected 501 while the buffer->Iceberg
  flush is unimplemented.
"""

from __future__ import annotations

import pytest

from app.domain.errors import PermanentJobError, UnsupportedConnectorError
from app.domain.probers import ProberRegistry, UnsupportedConnectorProber
from app.domain.querysource import (
    FakeQuerySource,
    QuerySourceRegistry,
    UnsupportedQuerySource,
)
from tests.util import TENANT_A, create_connection, outbox_events

ROWS = [{"id": i, "name": f"n{i}"} for i in range(3)]

PRESTO_CONNECTION = {
    "name": "Legacy Presto",
    "connector_type": "presto",
    "config": {"host": "presto.acme.internal", "catalog": "hive", "username": "ro"},
    "secrets": {"password": "pw"},
}


async def _run_query_ingestion(client, auth_a, container, name="Orders"):
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM public.orders",
            "new_dataset": {"name": name},
        },
        headers=auth_a,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]


# --------------------------------------------------------------- URN contract


async def test_completed_event_carries_minted_dataset_id(client, auth_a, container) -> None:
    """The completion event's dataset_id must be exactly the id embedded in the
    dataset_urn ingestion minted — dataset-service auto-registers under it."""
    job = await _run_query_ingestion(client, auth_a, container)
    assert job["status"] == "completed"
    urn = job["dataset_urn"]
    minted_id = urn.rsplit("/", 1)[1]

    events = await outbox_events(container, TENANT_A, "ingestion.completed")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["dataset_urn"] == urn
    assert payload["dataset_id"] == minted_id
    # and the bronze table embeds the same id (BR-13)
    assert payload["iceberg_table"].endswith(f"ds_{minted_id}")


async def test_completed_event_dataset_id_for_existing_dataset_urn(
    client, auth_a, container
) -> None:
    """dataset_urn-targeted ingestions carry that URN's id, not a new one."""
    from app.ids import uuid7

    existing_id = uuid7()
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT 1",
            "dataset_urn": f"wr:{TENANT_A}:dataset:dataset/{existing_id}",
        },
        headers=auth_a,
    )
    assert resp.status_code == 202, resp.text
    events = await outbox_events(container, TENANT_A, "ingestion.completed")
    assert events[0]["payload"]["dataset_id"] == existing_id


# ------------------------------------------------- unsupported connector gate


async def test_unsupported_connector_create_rejected_422(client, auth_a, container) -> None:
    """With the real-container registry default, a presto connection can never
    'test OK' against a fake — create is rejected 422 UNSUPPORTED_CONNECTOR."""
    container.probers = ProberRegistry(default=UnsupportedConnectorProber())
    resp = await client.post("/api/v1/connections", json=PRESTO_CONNECTION, headers=auth_a)
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "UNSUPPORTED_CONNECTOR"
    assert "driver not available" in err["message"]


async def test_unsupported_connector_rejected_even_with_skip_test(
    client, auth_a, container
) -> None:
    container.probers = ProberRegistry(default=UnsupportedConnectorProber())
    resp = await client.post(
        "/api/v1/connections", json={**PRESTO_CONNECTION, "skip_test": True}, headers=auth_a
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_CONNECTOR"
    # nothing persisted
    listing = await client.get("/api/v1/connections", headers=auth_a)
    assert listing.json()["data"] == []


async def test_unsupported_connector_adhoc_test_rejected_422(
    client, auth_a, container
) -> None:
    container.probers = ProberRegistry(default=UnsupportedConnectorProber())
    resp = await client.post(
        "/api/v1/connections:test",
        json={k: PRESTO_CONNECTION[k] for k in ("connector_type", "config", "secrets")},
        headers=auth_a,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_CONNECTOR"


async def test_unsupported_query_source_fails_job_permanently(
    client, auth_a, container
) -> None:
    """A connection whose query driver is missing fails the job with a
    categorized honest error — never a silent zero-row 'completed'."""
    conn = await create_connection(client, auth_a)  # probe ok via fake prober
    container.query_sources = QuerySourceRegistry(default=UnsupportedQuerySource())
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM public.orders",
            "new_dataset": {"name": "NoDriver"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 202
    job = resp.json()["data"]
    assert job["status"] == "failed"
    assert "UNSUPPORTED_CONNECTOR" in job["error_log"]["message"]
    assert container.table_writer.all_snapshots() == []


async def test_unsupported_query_source_raises_permanent_error() -> None:
    class _Cfg:
        connector_type = "presto"

    src = UnsupportedQuerySource()
    with pytest.raises(PermanentJobError, match="UNSUPPORTED_CONNECTOR"):
        await src.columns(_Cfg(), {}, "SELECT 1")


def test_unsupported_connector_error_shape() -> None:
    err = UnsupportedConnectorError("presto")
    assert err.status == 422
    assert err.code == "UNSUPPORTED_CONNECTOR"
    assert "presto" in err.message


def test_real_container_defaults_are_unsupported(monkeypatch, tmp_path) -> None:
    """_build_real wires honest-failing defaults; presto (the only declared
    connector without a real driver) resolves to them, while every wired type
    resolves to a real driver, not a fake."""
    from app.config import Settings
    from app.container import _build_real
    from app.domain.probers import FakeConnectionProber
    from app.domain.querysource import FakeQuerySource as FQS

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/real.db",
        adapter_mode="real",
        data_dir=str(tmp_path / "data"),
    )
    c = _build_real(settings)
    assert isinstance(c.probers.get("presto"), UnsupportedConnectorProber)
    assert isinstance(c.query_sources.get("presto"), UnsupportedQuerySource)
    for ctype in ("postgres", "mysql", "snowflake", "bigquery", "s3"):
        assert not isinstance(c.probers.get(ctype), (FakeConnectionProber,
                                                     UnsupportedConnectorProber))
    for ctype in ("postgres", "mysql", "snowflake", "bigquery"):
        assert not isinstance(c.query_sources.get(ctype), (FQS, UnsupportedQuerySource))


# ------------------------------------------------------ webhook_batch honesty


async def test_webhook_batch_creation_rejected_501(client, auth_a) -> None:
    """The buffer->Iceberg flush is unimplemented, so accepting webhook_batch
    ingestions would accept events that never become dataset rows: honest 501."""
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "webhook_batch", "new_dataset": {"name": "hooks"}},
        headers=auth_a,
    )
    assert resp.status_code == 501, resp.text
    err = resp.json()["error"]
    assert err["code"] == "NOT_IMPLEMENTED"
    assert "flush to Iceberg" in err["message"]

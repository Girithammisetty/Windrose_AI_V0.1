"""AC-6 (real Kafka): a case.resolved event produced to the REAL Redpanda broker
is consumed by the memory-service consumer, anonymized, chunked, embedded via
REAL Ollama, and lands in the resolved_cases corpus in Postgres."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.container import build_container
from app.events.consumer import CASE_TOPIC, KafkaMemoryConsumer
from app.events.envelope import make_envelope
from tests.conftest import TENANT_A, make_settings
from tests.integration.conftest import KAFKA, OLLAMA_URL, REDIS_URL, _reachable_ollama

pytestmark = pytest.mark.integration


def _kafka_up() -> bool:
    import socket
    host, _, port = KAFKA.partition(":")
    try:
        with socket.create_connection((host, int(port or 9092)), timeout=3):
            return True
    except OSError:
        return False


async def test_ac6_case_resolved_consumed_from_real_kafka(app_engine, admin_engine, clock):
    if not _kafka_up():
        pytest.skip(f"Redpanda/Kafka unreachable at {KAFKA}")
    if not _reachable_ollama():
        pytest.skip(f"Ollama unreachable at {OLLAMA_URL}")

    from windrose_common.kafka import KafkaConfig, KafkaProducerClient

    app_sf = async_sessionmaker(app_engine, expire_on_commit=False)
    admin_sf = async_sessionmaker(admin_engine, expire_on_commit=False)
    settings = make_settings(use_real_adapters=True, kafka_bootstrap_servers=KAFKA,
                             redis_url=REDIS_URL, embeddings_base_url=OLLAMA_URL)
    c = build_container(settings, mode="sql", session_factory=app_sf,
                        admin_session_factory=admin_sf, clock=clock)
    await c.provisioning.provision(TENANT_A)  # creates resolved_cases corpus row

    case_id = uuid.uuid4().hex
    env = make_envelope(
        event_type="case.resolved", tenant_id=TENANT_A,
        actor={"type": "service", "id": "case-service"},
        resource_urn=f"wr:{TENANT_A}:case:case/{case_id}",
        payload={"resolution_narrative": "Resolved by Mr. John Smith as duplicate vendor entry",
                 "disposition": "confirmed", "evidence_summary": "invoice booked twice",
                 "case_type": "duplicate_invoice"})

    # Unique topic isolates this test's single message from any backlog on the
    # shared case.events.v1 topic; the handler dispatches by event_type, not topic.
    topic = f"{CASE_TOPIC}.memtest-{case_id}"
    producer = KafkaProducerClient(KafkaConfig(bootstrap_servers=KAFKA))
    await producer.start()
    await producer.send(topic, TENANT_A, env)

    # fresh consumer group so we read from the just-produced offset (earliest)
    runner = KafkaMemoryConsumer(topic, c.consumer, producer,
                                 group_id=f"memtest-{case_id}", bootstrap_servers=KAFKA)
    await runner.start()
    try:
        await runner.consume_batch(1, timeout_ms=15000)
    finally:
        await runner.stop()
        await producer.stop()

    chunks = await c.store.list_chunks(TENANT_A, "resolved_cases")
    mine = [ch for ch in chunks if case_id in ch.source_urn]
    assert mine, "case.resolved event was not ingested from Kafka"
    joined = " ".join(ch.content for ch in mine)
    assert "John Smith" not in joined  # anonymized before embedding
    assert all(len(ch.embedding) == 768 for ch in mine)  # real Ollama vectors

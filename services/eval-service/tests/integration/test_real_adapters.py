"""Real infra adapters with no stubs in the path (CONVENTIONS.md END STATE):

* Redpanda / Kafka (localhost:9092) — real ``eval.events.v1`` publish + consume
  through the shared ``windrose_common`` producer, and the outbox dispatcher.
* OPA sidecar (localhost:8281) — real allow/deny authorization decisions.

Each fixture auto-skips with a clear message when its dependency is unreachable."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

KAFKA = "localhost:9092"
OPA_URL = "http://localhost:8281"
EVENTS_TOPIC = "eval.events.v1"


def _reachable(url: str) -> bool:
    try:
        with httpx.Client(timeout=3.0) as c:
            c.get(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def _kafka_up() -> bool:
    import socket

    try:
        with socket.create_connection(("localhost", 9092), timeout=3):
            return True
    except OSError:
        return False


async def test_real_kafka_publish_and_consume_eval_event():
    if not _kafka_up():
        pytest.skip("Kafka/Redpanda unreachable at localhost:9092")
    from app.events.bus import KafkaEventBus
    from app.events.envelope import make_envelope

    bus = KafkaEventBus(KAFKA)
    tenant = str(uuid.uuid4())
    marker = str(uuid.uuid4())
    env = make_envelope(
        event_type="gate.completed",
        tenant_id=tenant,
        actor={"type": "service", "id": "eval-service"},
        resource_urn=f"wr:{tenant}:eval:gate/gr-x",
        payload={"gate_run_id": "gr-x", "gate_passed": True, "marker": marker},
    )
    try:
        await bus.publish(EVENTS_TOPIC, env)
    finally:
        pass

    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        EVENTS_TOPIC,
        bootstrap_servers=KAFKA,
        group_id=f"eval-itest-{uuid.uuid4()}",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    found = None
    try:
        import time

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and found is None:
            batch = await consumer.getmany(timeout_ms=1000, max_records=500)
            for _tp, msgs in batch.items():
                for m in msgs:
                    e = json.loads(m.value)
                    if (e.get("payload") or {}).get("marker") == marker:
                        found = e
    finally:
        await consumer.stop()
        await bus.aclose()
    assert found is not None, "eval.events.v1 envelope not found on real Redpanda"
    assert found["event_type"] == "gate.completed"
    assert found["tenant_id"] == tenant
    print(f"\n[REAL Redpanda] consumed eval.events.v1 gate.completed marker={marker}")


async def test_real_outbox_dispatch_to_kafka(container, clock):
    """A committed outbox row (from a real gate) is relayed to real Redpanda by the
    OutboxDispatcher — the exact runtime path (MASTER-FR-034)."""
    if not _kafka_up():
        pytest.skip("Kafka/Redpanda unreachable at localhost:9092")
    from app.domain.entities import CallCtx
    from app.events.bus import KafkaEventBus
    from app.store.sql import OutboxDispatcher

    # Produce a real outbox row: freeze a dataset (emits dataset.version_frozen).
    tenant = "11111111-1111-4111-8111-111111111111"
    ctx = CallCtx(tenant_id=tenant, actor={"type": "user", "id": "u"})
    await container.dataset_service.create(ctx, {"dataset_key": "k/x", "agent_key": "a"})
    await container.case_service.create(
        ctx,
        {
            "dataset_key": "k/x",
            "agent_key": "a",
            "input": {},
            "expected": {"kind": "rubric", "value": {}},
            "status": "active",
        },
    )
    await container.dataset_service.freeze(ctx, "k/x", 1)

    # Use the container's own session factory (sql mode stored in extras).
    session_factory = container.extras["session_factory"]
    bus = KafkaEventBus(KAFKA)
    dispatcher = OutboxDispatcher(session_factory, bus)
    n = await dispatcher.run_once()
    await bus.aclose()
    assert n >= 1, "outbox dispatcher relayed no rows"
    print(f"\n[REAL Redpanda] outbox dispatcher relayed {n} eval.events.v1 row(s)")


async def test_real_opa_authorization_decision():
    if not _reachable(f"{OPA_URL}/health"):
        pytest.skip("OPA unreachable at localhost:8281")
    from windrose_common.opaclient import OpaClient

    opa = OpaClient(OPA_URL)
    tenant = str(uuid.uuid4())
    allow_projection = {
        "action_known": True,
        "action_scoped": False,
        "autonomous_enabled": False,
        "flags": {"found": False, "admin": False, "ws_admin": []},
        "tenant_actions": {"found": True, "actions": ["eval.gate.read"]},
        "workspace": {"assigned": False, "actions": [], "archived": False},
        "resource": {"found": False, "level": "", "archived": False},
        "workspace_archived_tenant": False,
    }
    allow = await opa.decision(
        subject={"id": "user-1", "typ": "user", "scopes": ["eval.gate.read"]},
        action="eval.gate.read",
        tenant=tenant,
        projection=allow_projection,
    )
    assert allow.allow is True, f"expected real OPA allow, got {allow}"
    deny = await opa.decision(
        subject={"id": "user-1", "typ": "user", "scopes": []},
        action="eval.gate.read",
        tenant=tenant,
        projection={**allow_projection, "action_known": False},
    )
    assert deny.allow is False
    print(f"\n[REAL OPA] allow={allow.allow}/{allow.reason} deny={deny.allow}/{deny.reason}")

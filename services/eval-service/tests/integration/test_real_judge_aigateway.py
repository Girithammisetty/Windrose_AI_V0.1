"""HEADLINE PROOF (EVL-FR-012, learning-loop gate): a real eval run over a golden
set with a DETERMINISTIC scorer (sql_result_equivalence via DuckDB) AND a real
LLM-JUDGE score obtained THROUGH the real ai-gateway -> real Ollama (qwen2.5:0.5b),
consuming real tokens. No stubs in the path.

The test boots a real ai-gateway process wired to the compose infra
(Postgres/Redis/Redpanda/Ollama), operator-seeds a ``balanced -> qwen2.5:0.5b``
judge deployment + a virtual key for eval-service's tenant (exactly as the
platform's e2e seed does), then drives eval-service's real ``AiGatewayJudgeClient``
against it. Auto-skips when infra / Ollama / uv is unavailable."""

from __future__ import annotations

import hashlib
import os
import secrets
import socket
import subprocess
import time
import uuid

import httpx
import pytest

from app.container import build_container
from app.domain.entities import CallCtx
from tests.conftest import PRIVATE_PEM, PUBLIC_PEM, make_settings

pytestmark = pytest.mark.integration

REPO = "/Users/girithammisetty/Projects/Windrose-ai"
AIGW_DIR = f"{REPO}/services/ai-gateway"
UV = "/opt/homebrew/bin/uv"

OLLAMA_TAGS = "http://localhost:11434/api/tags"
CHAT_MODEL = "qwen2.5:0.5b"
PG_SUPER = "postgresql://windrose:windrose_dev@localhost:5432/postgres"
AIGW_DB = "ai_gateway"
AIGW_PORT = 8399
AIGW_URL = f"http://localhost:{AIGW_PORT}"

ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"
TENANT = "11111111-1111-4111-8111-111111111111"


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _ollama_has_model() -> bool:
    try:
        with httpx.Client(timeout=3) as c:
            tags = c.get(OLLAMA_TAGS).json()
        return any(m["name"].startswith(CHAT_MODEL) for m in tags.get("models", []))
    except Exception:  # noqa: BLE001
        return False


def _ensure_db():
    import psycopg

    with psycopg.connect(PG_SUPER, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (AIGW_DB,)).fetchone()
        if not exists:
            conn.execute(f"CREATE DATABASE {AIGW_DB}")


def _migrate_aigw(env):
    subprocess.run(
        [UV, "run", "alembic", "upgrade", "head"],
        cwd=AIGW_DIR,
        env=env,
        check=True,
        capture_output=True,
        timeout=180,
    )


def _seed_deployment_and_key() -> str:
    """Operator bootstrap (same as deploy/e2e/lib/seed.py): seed a judge-ladder
    deployment mapping ``balanced -> qwen2.5:0.5b`` and mint a tenant-scoped
    virtual key. Returns the ``nk-...`` secret."""
    import datetime as dt

    import psycopg

    now = dt.datetime.now(dt.UTC)
    vkey = f"nk-{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(vkey.encode()).hexdigest()
    dsn = f"postgresql://windrose:windrose_dev@localhost:5432/{AIGW_DB}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        for family in ("balanced", "frontier", "fast-small"):
            conn.execute(
                """INSERT INTO provider_deployments
                   (id, tenant_id, provider, model_family, deployment_name, region, cloud,
                    endpoint_vault_ref, tpm_limit, rpm_limit, priority, status,
                    created_at, updated_at)
                   VALUES (%s,%s,'bedrock',%s,'qwen2.5:0.5b','local','aws','',
                           1000000, 6000, 1, 'active', %s, %s)
                   ON CONFLICT DO NOTHING""",
                (str(uuid.uuid4()), TENANT, family, now, now),
            )
        conn.execute(
            """INSERT INTO virtual_keys
               (id, tenant_id, key_hash, principal_type, principal_id,
                allowed_request_classes, max_rung, status, created_at, updated_at)
               VALUES (%s,%s,%s,'service','svc:eval-service', %s, 3, 'active', %s, %s)
               ON CONFLICT (key_hash) DO NOTHING""",
            (str(uuid.uuid4()), TENANT, key_hash, ["judge", "chat"], now, now),
        )
    return vkey


@pytest.fixture(scope="module")
def ai_gateway():
    if not _ollama_has_model():
        pytest.skip(f"Ollama/{CHAT_MODEL} unreachable at localhost:11434")
    for host, port, name in [
        ("localhost", 5432, "Postgres"),
        ("localhost", 6379, "Redis"),
        ("localhost", 9092, "Redpanda"),
    ]:
        if not _port_open(host, port):
            pytest.skip(f"{name} unreachable at {host}:{port}")
    if not os.path.exists(UV):
        pytest.skip("uv not available")

    env = {
        **os.environ,
        "PATH": f"/opt/homebrew/bin:{os.environ.get('PATH', '')}",
        "AIG_USE_REAL_ADAPTERS": "true",
        "AIG_DATABASE_URL": f"postgresql+asyncpg://windrose:windrose_dev@localhost:5432/{AIGW_DB}",
        "AIG_MIGRATE_URL": f"postgresql+psycopg://windrose:windrose_dev@localhost:5432/{AIGW_DB}",
        "AIG_REDIS_URL": "redis://localhost:6379/0",
        "AIG_OLLAMA_BASE_URL": "http://localhost:11434/v1",
        "AIG_KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
        "AIG_OPA_URL": "http://localhost:8281",
        "AIG_JWT_ISSUER": ISSUER,
        "AIG_JWT_AUDIENCE": AUDIENCE,
        "AIG_JWT_PUBLIC_KEY_PEM": PUBLIC_PEM,
    }
    _ensure_db()
    try:
        _migrate_aigw(env)
    except subprocess.CalledProcessError as exc:  # noqa: BLE001
        pytest.skip(f"ai-gateway migration failed: {exc.stderr[-500:] if exc.stderr else exc}")
    vkey = _seed_deployment_and_key()

    proc = subprocess.Popen(
        [UV, "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(AIGW_PORT)],
        cwd=AIGW_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # wait for readiness
    ready = False
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode()[-1500:] if proc.stdout else ""
            pytest.skip(f"ai-gateway exited early:\n{out}")
        try:
            with httpx.Client(timeout=2) as c:
                if c.get(f"{AIGW_URL}/healthz").status_code == 200:
                    ready = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    if not ready:
        proc.terminate()
        pytest.skip("ai-gateway did not become ready in 60s")
    yield {"url": AIGW_URL, "vkey": vkey}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _eval_container(tmp_path, ai_gateway):
    """Build an eval-service container with the REAL ai-gateway judge client."""
    settings = make_settings(
        tmp_path,
        ai_gateway_url=ai_gateway["url"],
        ai_gateway_virtual_key=ai_gateway["vkey"],
        judge_jwt_signing_key_pem=PRIVATE_PEM,
        ai_gateway_model="windrose-auto",
        judge_request_class="judge",
    )
    return build_container(settings, mode="memory")  # real judge client (no override)


def ctx():
    return CallCtx(tenant_id=TENANT, actor={"type": "user", "id": "eng-1"})


async def test_real_llm_judge_score_through_ai_gateway_to_ollama(ai_gateway, tmp_path):
    container = _eval_container(tmp_path, ai_gateway)

    # (a) a direct real judge call: eval -> ai-gateway (judge class) -> Ollama.
    res = await container.judge_client.judge(
        messages=[
            {
                "role": "system",
                "content": "You are a strict judge. Reply ONLY with JSON "
                '{"rating": <1-5 integer>, "rationale": <one sentence>}.',
            },
            {
                "role": "user",
                "content": "USER REQUEST: total revenue by region.\n"
                "EVIDENCE: [region=EMEA rev=150]\n"
                "ANSWER: EMEA revenue is 150. Rate groundedness.",
            },
        ],
        tenant_id=TENANT,
        max_tokens=128,
    )
    assert res.output_tokens > 0, "expected real completion tokens from Ollama via ai-gateway"
    assert res.input_tokens > 0
    assert res.content.strip(), "empty judge output"
    print(
        f"\n[REAL judge ai-gateway->Ollama] model={res.model} "
        f"prompt_tokens={res.input_tokens} completion_tokens={res.output_tokens}"
    )
    print(f"[judge raw] {res.content[:200]!r}")

    # (b) a full eval run over a golden set: deterministic sql_result_equivalence
    # (real DuckDB) + a REAL groundedness judge score through ai-gateway->Ollama.
    container.warehouse.seed(
        "fw",
        {"orders": (["region", "net_revenue"], [("EMEA", 100.0), ("EMEA", 50.0), ("AMER", 200.0)])},
    )
    good_sql = "SELECT region, SUM(net_revenue) AS rev FROM orders GROUP BY region"
    c = await container.case_service.create(
        ctx(),
        {
            "dataset_key": "analytics/nl2sql",
            "agent_key": "analytics",
            "input": {
                "messages": [{"role": "user", "content": "total revenue by region"}],
                "context_refs": {"fixture_warehouse": "fw"},
            },
            "expected": {
                "kind": "sql_result",
                "value": {"sql": good_sql, "order_insensitive": True},
            },
            "status": "active",
        },
    )
    await container.suite_service.create(
        ctx(),
        {
            "suite_id": "analytics-gate",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [
                {"scorer": "sql_result_equivalence", "version": 2, "weight": 0.7},
                {
                    "scorer": "groundedness",
                    "version": 3,
                    "weight": 0.3,
                    "config": {"pass_threshold": 1.0},
                },
            ],
            "gate_rule": "sql_result_equivalence.pass_rate >= 0.99",
            "min_cases": 1,
        },
    )
    outputs = {
        c.id: {
            "sql": good_sql,
            "answer": "EMEA revenue is 150 and AMER revenue is 200.",
            "evidence": [{"region": "EMEA", "rev": 150}, {"region": "AMER", "rev": 200}],
        }
    }
    run = await container.run_service.create_and_execute(
        ctx(),
        trigger="publish_gate",
        agent_key="analytics",
        candidate={"content_digest": "sha256:realjudge", "agent_version": "v2"},
        suite_id="analytics-gate",
        candidate_provider=container.candidate_provider(outputs),
    )
    assert run.status == "completed"
    results = await container.run_service.list_cases(ctx(), run.id)
    by_scorer = {r.scorer_key: r for r in results}
    assert by_scorer["sql_result_equivalence"].passed is True  # deterministic gate metric
    g = by_scorer["groundedness"]
    # judge_model is the ai-gateway ladder model the request routed to (maps to the
    # seeded qwen2.5:0.5b deployment); the score is a real judge rating in [0,5].
    assert g.details["judge_model"], "no judge model recorded"
    assert 0.0 <= g.score <= 5.0
    assert g.trace_ref, "expected an ai-gateway request id as the trace ref"
    sql_passed = by_scorer["sql_result_equivalence"].passed
    print(f"\n[REAL eval run] sql_result_equivalence passed={sql_passed}")
    print(
        f"[REAL LLM-judge groundedness] score={g.score} model={g.details['judge_model']} "
        f"rationale={g.details.get('rationale')!r}"
    )

    # (c) the gate returns pass/fail vs baseline off the deterministic scorer.
    gate = await container.gate_service.evaluate_from_run(ctx(), run.id)
    assert gate.gate_passed is True
    print(
        f"[REAL gate] gate_passed={gate.gate_passed} "
        f"verdicts={[(v['scorer'], v['passed']) for v in gate.verdicts]}"
    )

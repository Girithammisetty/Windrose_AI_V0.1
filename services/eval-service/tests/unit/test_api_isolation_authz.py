"""API happy-path, tenant isolation (cross-tenant 404) and authz matrix
(MASTER-FR §2.8 / AC-3 / AC-14)."""

from __future__ import annotations

from tests.conftest import TENANT_A, TENANT_B, auth


async def test_missing_token_401(client):
    r = await client.post("/api/v1/datasets", json={"dataset_key": "a/b", "agent_key": "a"})
    assert r.status_code == 401


async def test_authz_denies_without_scope(client):
    # a token with an unrelated scope cannot write datasets
    r = await client.post(
        "/api/v1/datasets",
        json={"dataset_key": "a/b", "agent_key": "a"},
        headers=auth(scopes=["eval.case.read"]),
    )
    assert r.status_code == 403


async def test_dataset_crud_and_suite_gate_rule_validation(client):
    r = await client.post(
        "/api/v1/datasets",
        json={"dataset_key": "analytics/nl2sql", "agent_key": "analytics"},
        headers=auth(),
    )
    assert r.status_code == 201, r.text

    # AC-3: suite gate rule with only a judge term -> 422
    bad = await client.post(
        "/api/v1/suites",
        json={
            "suite_id": "s1",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [{"scorer": "groundedness", "version": 3}],
            "gate_rule": "groundedness.mean >= baseline - 0.3",
            "min_cases": 1,
        },
        headers=auth(),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "JUDGE_GATES_ALONE"

    # BR-1 OR-bypass: an OR rule where a judge term can carry the gate alone -> 422
    or_bypass = await client.post(
        "/api/v1/suites",
        json={
            "suite_id": "s1",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [
                {"scorer": "schema_validity", "version": 1},
                {"scorer": "groundedness", "version": 3},
            ],
            "gate_rule": "schema_validity.pass_rate >= 0.9 OR groundedness.mean >= 3.0",
            "min_cases": 1,
        },
        headers=auth(),
    )
    assert or_bypass.status_code == 422
    assert or_bypass.json()["error"]["code"] == "JUDGE_GATES_ALONE"

    ok = await client.post(
        "/api/v1/suites",
        json={
            "suite_id": "s1",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [
                {"scorer": "schema_validity", "version": 1},
                {"scorer": "groundedness", "version": 3},
            ],
            "gate_rule": "schema_validity.pass_rate >= 0.9 AND groundedness.mean >= baseline - 0.3",
            "min_cases": 1,
        },
        headers=auth(),
    )
    assert ok.status_code == 201, ok.text


async def test_cross_tenant_isolation_404(client):
    # tenant A creates a case
    created = await client.post(
        "/api/v1/cases",
        json={
            "dataset_key": "k/x",
            "agent_key": "a",
            "input": {},
            "expected": {"kind": "rubric", "value": {}},
        },
        headers=auth(TENANT_A),
    )
    assert created.status_code == 201
    case_id = created.json()["data"]["id"]
    # tenant B cannot see it -> 404 (not 403)
    other = await client.get(f"/api/v1/cases/{case_id}", headers=auth(TENANT_B))
    assert other.status_code == 404


async def test_ci_gate_endpoint_end_to_end(client):
    # dataset + active case + suite, then CI evaluate produces a gate verdict.
    await client.post(
        "/api/v1/cases",
        json={
            "dataset_key": "analytics/nl2sql",
            "agent_key": "analytics",
            "input": {"messages": [{"role": "user", "content": "q"}]},
            "expected": {"kind": "structured", "value": {"schema": {"type": "object"}}},
            "status": "active",
        },
        headers=auth(),
    )
    await client.post(
        "/api/v1/suites",
        json={
            "suite_id": "analytics-gate",
            "agent_key": "analytics",
            "datasets": [{"dataset_key": "analytics/nl2sql", "version": 1}],
            "scorers": [{"scorer": "schema_validity", "version": 1}],
            "gate_rule": "schema_validity.pass_rate >= 0.9",
            "min_cases": 1,
        },
        headers=auth(),
    )
    # need candidate outputs keyed by case id
    cases = await client.get(
        "/api/v1/cases?filter[status]=active&filter[dataset_key]=analytics/nl2sql", headers=auth()
    )
    case_id = cases.json()["data"][0]["id"]
    r = await client.post(
        "/api/v1/ci/evaluate",
        json={
            "agent_key": "analytics",
            "build_digest": "sha256:abc",
            "suite_id": "analytics-gate",
            "candidate_outputs": {case_id: {"structured": {"any": "thing"}}},
        },
        headers=auth(),
    )
    assert r.status_code == 202, r.text
    gate_run_id = r.json()["data"]["gate_run_id"]
    g = await client.get(f"/api/v1/gates/{gate_run_id}", headers=auth())
    assert g.status_code == 200
    assert g.json()["data"]["gate_passed"] is True
    # idempotent reuse on same digest (AC-7)
    again = await client.post(
        "/api/v1/ci/evaluate",
        json={
            "agent_key": "analytics",
            "build_digest": "sha256:abc",
            "suite_id": "analytics-gate",
            "candidate_outputs": {case_id: {"structured": {"any": "thing"}}},
        },
        headers=auth(),
    )
    assert again.json()["data"]["reused"] is True

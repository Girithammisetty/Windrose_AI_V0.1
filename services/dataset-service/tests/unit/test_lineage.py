"""Unit: lineage graph ops — depth caps, node caps, cycle safety/rejection
(DST-FR-040..043, BR-6/BR-7, AC-7, AC-8)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.entities import LineageEdge
from app.domain.lineage import traverse, would_create_cycle
from app.store.memory import MemoryLineageRepo, MemoryState
from tests.conftest import TENANT_A, TENANT_B, auth

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def edge(state: MemoryState, from_urn: str, to_urn: str, activity="derived",
         tenant=TENANT_A, run_urn=None):
    e = LineageEdge(
        id=f"e{len(state.edges)}", tenant_id=tenant, from_urn=from_urn, to_urn=to_urn,
        activity=activity, run_urn=run_urn, occurred_at=NOW, created_at=NOW,
    )
    state.edges[e.id] = e
    return e


def urn(name: str, tenant=TENANT_A) -> str:
    return f"wr:{tenant}:pipeline:run/{name}"


class TestTraversal:
    async def test_depth_cap_limits_hops(self):
        state = MemoryState()
        # chain n0 -> n1 -> n2 -> n3 -> n4
        for i in range(4):
            edge(state, urn(f"n{i}"), urn(f"n{i + 1}"))
        repo = MemoryLineageRepo(state, TENANT_A)
        result = await traverse(repo, {urn("n0")}, direction="downstream", depth=2,
                                activities=None, node_cap=1000)
        assert len(result.edges) == 2
        assert result.truncated is True  # more graph beyond the requested depth
        full = await traverse(repo, {urn("n0")}, direction="downstream", depth=10,
                              activities=None, node_cap=1000)
        assert len(full.edges) == 4
        assert full.truncated is False

    async def test_node_cap_truncates(self):
        state = MemoryState()
        for i in range(30):
            edge(state, urn("root"), urn(f"leaf{i}"))
        repo = MemoryLineageRepo(state, TENANT_A)
        result = await traverse(repo, {urn("root")}, direction="downstream", depth=3,
                                activities=None, node_cap=10)
        assert len(result.nodes) == 10
        assert result.truncated is True

    async def test_cycle_safe_traversal_terminates(self):
        """BR-7: stored cycles never hang a query."""
        state = MemoryState()
        edge(state, urn("a"), urn("b"))
        edge(state, urn("b"), urn("c"))
        edge(state, urn("c"), urn("a"))  # cycle
        repo = MemoryLineageRepo(state, TENANT_A)
        result = await traverse(repo, {urn("a")}, direction="downstream", depth=10,
                                activities=None, node_cap=1000)
        assert result.nodes == {urn("a"), urn("b"), urn("c")}
        assert len(result.edges) == 3

    async def test_activity_filter(self):
        state = MemoryState()
        edge(state, urn("a"), urn("b"), activity="trained")
        edge(state, urn("a"), urn("c"), activity="derived")
        repo = MemoryLineageRepo(state, TENANT_A)
        result = await traverse(repo, {urn("a")}, direction="downstream", depth=3,
                                activities=["trained"], node_cap=1000)
        assert [e.activity for e in result.edges] == ["trained"]

    async def test_would_create_cycle(self):
        state = MemoryState()
        edge(state, urn("a"), urn("b"))
        edge(state, urn("b"), urn("c"))
        repo = MemoryLineageRepo(state, TENANT_A)
        # c -> a closes the loop a->b->c->a: a is downstream-reachable from...
        # (from_urn=c is reachable downstream of to_urn=a via a->b->c)
        assert await would_create_cycle(repo, from_urn=urn("c"), to_urn=urn("a")) is True
        assert await would_create_cycle(repo, from_urn=urn("a"), to_urn=urn("c")) is False
        assert await would_create_cycle(repo, from_urn=urn("a"), to_urn=urn("a")) is True


class TestLineageApi:
    async def _seed_chain(self, client, tenant=TENANT_A):
        """AC-7 chain: connection -> ingestion -> dataset -> run -> model -> inference."""
        t = tenant
        chain = [
            (f"wr:{t}:ingestion:connection/c1", f"wr:{t}:ingestion:ingestion/i1", "ingested"),
            (f"wr:{t}:ingestion:ingestion/i1", f"wr:{t}:dataset:dataset/d1", "ingested"),
            (f"wr:{t}:dataset:dataset/d1", f"wr:{t}:pipeline:run/r1", "transformed"),
            (f"wr:{t}:pipeline:run/r1", f"wr:{t}:experiment:model/m1", "trained"),
            (f"wr:{t}:experiment:model/m1", f"wr:{t}:inference:job/j1", "inferred"),
        ]
        for from_urn, to_urn, activity in chain:
            resp = await client.post(
                "/api/v1/lineage/edges",
                json={"from_urn": from_urn, "to_urn": to_urn, "activity": activity},
                headers=auth(t),
            )
            assert resp.status_code == 201, resp.text
        return chain

    async def test_ac7_upstream_depth(self, client):
        """AC-7: five hops at depth 10 with truncated=false; depth 2 truncated=true."""
        t = TENANT_A
        await self._seed_chain(client)
        resp = await client.get(
            "/api/v1/lineage",
            params={"urn": f"wr:{t}:inference:job/j1", "direction": "upstream",
                    "depth": 10},
            headers=auth(t),
        )
        data = resp.json()["data"]
        assert resp.status_code == 200
        assert len(data["edges"]) == 5
        assert data["truncated"] is False
        assert {e["activity"] for e in data["edges"]} == {
            "ingested", "transformed", "trained", "inferred"
        }

        resp = await client.get(
            "/api/v1/lineage",
            params={"urn": f"wr:{t}:inference:job/j1", "direction": "upstream", "depth": 2},
            headers=auth(t),
        )
        data = resp.json()["data"]
        assert len(data["edges"]) == 2
        assert data["truncated"] is True

    async def test_depth_over_10_rejected(self, client):
        resp = await client.get(
            "/api/v1/lineage",
            params={"urn": f"wr:{TENANT_A}:dataset:dataset/d1", "depth": 11},
            headers=auth(),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_FAILED"

    async def test_bad_urn_rejected(self, client):
        resp = await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": "not-a-urn", "to_urn": f"wr:{TENANT_A}:dataset:dataset/d",
                  "activity": "derived"},
            headers=auth(),
        )
        assert resp.status_code == 422

    async def test_bad_activity_rejected(self, client):
        resp = await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": f"wr:{TENANT_A}:dataset:dataset/a",
                  "to_urn": f"wr:{TENANT_A}:dataset:dataset/b", "activity": "munged"},
            headers=auth(),
        )
        assert resp.status_code == 422

    async def test_self_edge_rejected(self, client):
        u = f"wr:{TENANT_A}:dataset:dataset/a"
        resp = await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": u, "to_urn": u, "activity": "derived"},
            headers=auth(),
        )
        assert resp.status_code == 422

    async def test_cycle_rejected_on_write(self, client):
        t = TENANT_A
        a, b, c = (f"wr:{t}:pipeline:run/{n}" for n in "abc")
        for from_urn, to_urn in [(a, b), (b, c)]:
            await client.post(
                "/api/v1/lineage/edges",
                json={"from_urn": from_urn, "to_urn": to_urn, "activity": "derived"},
                headers=auth(t),
            )
        resp = await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": c, "to_urn": a, "activity": "derived"},
            headers=auth(t),
        )
        assert resp.status_code == 422
        assert "cycle" in resp.json()["error"]["message"]

    async def test_duplicate_edge_idempotent_upsert(self, client, container):
        t = TENANT_A
        body = {"from_urn": f"wr:{t}:pipeline:run/x",
                "to_urn": f"wr:{t}:dataset:dataset/y",
                "activity": "transformed", "run_urn": f"wr:{t}:pipeline:run/x"}
        first = await client.post("/api/v1/lineage/edges", json=body, headers=auth(t))
        second = await client.post("/api/v1/lineage/edges", json=body, headers=auth(t))
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["data"]["created"] is False
        assert len(container.memory_state.edges) == 1
        assert len(container.memory_state.events_of_type("lineage.edge_created")) == 1

    async def test_ac8_cross_tenant_write_404_and_audited(self, client, container):
        """AC-8: foreign-tenant URN -> 404 + security.cross_tenant_denied audit."""
        resp = await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": f"wr:{TENANT_A}:pipeline:run/r1",
                  "to_urn": f"wr:{TENANT_B}:dataset:dataset/d1",
                  "activity": "derived"},
            headers=auth(TENANT_A),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
        audits = container.memory_state.events_of_type("security.cross_tenant_denied")
        assert len(audits) == 1
        assert audits[0]["tenant_id"] == TENANT_A

    async def test_lineage_query_isolated_by_tenant(self, client):
        await self._seed_chain(client)
        resp = await client.get(
            "/api/v1/lineage",
            params={"urn": f"wr:{TENANT_A}:inference:job/j1", "depth": 5,
                    "direction": "upstream"},
            headers=auth(TENANT_B),
        )
        assert resp.status_code == 404  # foreign tenant urn -> 404, no leak

    async def test_node_enrichment_kinds(self, client, container):
        t = TENANT_A
        ds = (await client.post(
            "/api/v1/datasets",
            json={"workspace_id": "33333333-3333-4333-8333-333333333333", "name": "Enrich"},
            headers=auth(t),
        )).json()["data"]
        await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": f"wr:{t}:ingestion:ingestion/i9",
                  "to_urn": ds["urn"], "activity": "ingested"},
            headers=auth(t),
        )
        resp = await client.get(
            "/api/v1/lineage",
            params={"urn": ds["urn"], "direction": "upstream", "depth": 3},
            headers=auth(t),
        )
        nodes = {n["urn"]: n for n in resp.json()["data"]["nodes"]}
        assert nodes[ds["urn"]]["kind"] == "dataset"
        assert nodes[ds["urn"]]["name"] == "Enrich"
        assert nodes[f"wr:{t}:ingestion:ingestion/i9"]["kind"] == "foreign"

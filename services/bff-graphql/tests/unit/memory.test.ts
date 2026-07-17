/**
 * Memory browse + right-to-be-forgotten erasure + stats (memory-service).
 * Response shapes mirror the real downstream route bodies — see
 * services/memory-service/app/api/routes/{memories,admin}.py.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function memory() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/memories" && req.method === "GET") {
      expect(req.search.get("scope")).toBe("workspace");
      expect(req.search.get("scope_ref")).toBe("ws-1");
      return {
        status: 200,
        body: {
          data: [
            { memory_id: "m-1", scope: "workspace", scope_ref: "ws-1", content: "the claim total is $4,200",
              confidence: 0.91, status: "active", tags: ["claims"], retrieval_count: 3, classifier_score: 0.1,
              ttl_expires_at: "2026-08-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/memories/m-1" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: { memory_id: "m-1", scope: "workspace", scope_ref: "ws-1", content: "the claim total is $4,200",
            confidence: 0.91, status: "active", tags: ["claims"], retrieval_count: 3, classifier_score: 0.1,
            ttl_expires_at: "2026-08-01T00:00:00Z", merged_from: [], revalidate_at: null },
        },
      };
    }
    if (req.path === "/api/v1/erasure" && req.method === "POST") {
      expect(req.body).toMatchObject({ subject_type: "user", subject_id: "u-42" });
      return { status: 202, body: { data: { operation_id: "op-1", status: "received" } } };
    }
    if (req.path === "/api/v1/erasure/op-1" && req.method === "GET") {
      return { status: 200, body: { data: { operation_id: "op-1", status: "completed", report: { erased: 4 }, completed_at: "2026-07-12T00:00:00Z" } } };
    }
    if (req.path === "/api/v1/stats" && req.method === "GET") {
      return { status: 200, body: { data: { total_records: 42, quarantined: 1 } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = memory();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("memory: browse + detail", () => {
  it("browses memories scoped to a workspace", async () => {
    const { body } = await run(
      `{ memories(scope: "workspace", scopeRef: "ws-1") { nodes { id scope scopeRef content status confidence retrievalCount } } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).memories.nodes[0]).toMatchObject({
      id: "m-1", scope: "workspace", scopeRef: "ws-1", content: "the claim total is $4,200",
      status: "active", confidence: 0.91, retrievalCount: 3,
    });
  });

  it("reads a single memory record with full detail", async () => {
    const { body } = await run(`{ memory(id: "m-1") { id content mergedFrom revalidateAt } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).memory).toMatchObject({ id: "m-1", content: "the claim total is $4,200", mergedFrom: [] });
  });
});

describe("memory: right-to-be-forgotten erasure", () => {
  it("requestMemoryErasure POSTs the subject and returns the operation", async () => {
    const { body, requests } = await run(
      `mutation($id:String!){ requestMemoryErasure(subjectId: $id) { operationId status } }`,
      { id: "u-42" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).requestMemoryErasure).toMatchObject({ operationId: "op-1", status: "received" });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/erasure")).toBe(true);
  });

  it("erasure(id) polls the real status/report", async () => {
    const { body } = await run(`{ erasure(id: "op-1") { operationId status report completedAt } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).erasure).toMatchObject({
      operationId: "op-1", status: "completed", report: { erased: 4 }, completedAt: "2026-07-12T00:00:00Z",
    });
  });
});

describe("memory: stats", () => {
  it("passes through the opaque stats dict", async () => {
    const { body } = await run(`{ memoryStats }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).memoryStats).toEqual({ total_records: 42, quarantined: 1 });
  });
});

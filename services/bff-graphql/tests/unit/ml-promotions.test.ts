/**
 * Model promotion approval queue (four-eyes). Response shapes mirror the real
 * downstream route bodies — see
 * services/experiment-service/app/api/routes/promotions.py.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function experiment() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/models/m-1/versions/2/promotions" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "p-1", urn: "wr:t:experiment:promotion/p-1", model_version_id: "m-1@2",
              target_stage: "production", from_stage: "staging", status: "pending",
              rationale: "Beats baseline on F1", requested_by: "user:u-1", via_agent: null,
              decision: null, created_at: "2026-07-12T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/models/m-1/versions/2/promote" && req.method === "POST") {
      return {
        status: 202,
        operation_id: "op-1",
        body: { operation_id: "op-1", data: { promotion_id: "p-new", status: "pending" } },
      };
    }
    if (req.path === "/api/v1/promotions/p-1/decision" && req.method === "POST") {
      return { status: 200, body: { data: { id: "p-1", status: "approved" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-2", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = experiment();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("ml: promotion approval queue", () => {
  it("lists a model version's promotions", async () => {
    const { body } = await run(
      `{ promotions(modelId: "m-1", version: 2) { nodes { id targetStage fromStage status rationale requestedBy } } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).promotions.nodes[0]).toMatchObject({
      id: "p-1", targetStage: "production", fromStage: "staging", status: "pending",
      rationale: "Beats baseline on F1", requestedBy: "user:u-1",
    });
  });

  it("promoteModelVersion POSTs the real target stage (not hardcoded to production)", async () => {
    const { body, requests } = await run(
      `mutation($k:String){ promoteModelVersion(modelId: "m-1", version: 2, targetStage: "staging", idempotencyKey: $k) { promotionId status } }`,
      { k: "idem-1" },
    );
    expect(body?.errors).toBeUndefined();
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/models/m-1/versions/2/promote");
    expect(post?.body).toMatchObject({ target_stage: "staging" });
  });

  it("decidePromotion approves a pending promotion", async () => {
    const { body, requests } = await run(
      `mutation { decidePromotion(promotionId: "p-1", decision: "approve", message: "LGTM") }`,
    );
    expect(body?.errors).toBeUndefined();
    expect(body?.data).toMatchObject({ decidePromotion: { id: "p-1", status: "approved" } });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/promotions/p-1/decision");
    expect(post?.body).toMatchObject({ decision: "approve", message: "LGTM" });
  });
});

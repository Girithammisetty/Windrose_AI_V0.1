import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** semantic-service double: model list (headers, page envelope) + published
 * definition (entities/dimensions/measures). Mirrors app/api/routes/models.py. */
function semantic() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/models" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "sm-1", workspace_id: "ws-9", name: "claims_core", description: "claims",
              published_version_id: "ver-1", published_version_no: 3,
              health: { status: "ok", broken_refs: [] },
              created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" },
            { id: "sm-2", workspace_id: "ws-9", name: "vendors", published_version_no: null,
              created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" },
          ],
          page: { next_cursor: null, has_more: false },
        },
      };
    }
    // published definition for claims_core (dimensions/measures per bootstrap shape)
    if (req.path === "/api/v1/models/sm-1/definition" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: {
            version_no: 3,
            definition: {
              entities: [{ name: "claim", dataset_urn: "wr:t:dataset:dataset/d1" }],
              dimensions: [
                { name: "claim_type", entity: "claim", column: "claim_type", type: "categorical",
                  time_grains: [], synonyms: [], origin: "bootstrap" },
                { name: "vendor", entity: "claim", column: "vendor", type: "categorical",
                  time_grains: [], synonyms: [], origin: "bootstrap" },
              ],
              measures: [
                { name: "claim_count", entity: "claim", agg: "count", expr: null,
                  synonyms: [], origin: "bootstrap" },
              ],
            },
          },
        },
      };
    }
    // unpublished model → 409 MODEL_NOT_PUBLISHED
    if (req.path === "/api/v1/models/sm-2/definition" && req.method === "GET") {
      return { status: 409, body: { error: { code: "MODEL_NOT_PUBLISHED", message: "no published version", trace_id: "t" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("semantic model resolvers (semantic-service passthrough, JWT forwarded)", () => {
  it("semanticModels lists headers with empty dims/measures + a semantic URN (no N+1)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semantic();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-9" });
    const res = await server.executeOperation(
      { query: `query($ws: ID) { semanticModels(workspaceId: $ws) { id name urn dimensions { name } measures { name } } }`,
        variables: { ws: "ws-9" } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const models: any[] = (body?.data as any).semanticModels;
    expect(models.map((m) => m.name)).toEqual(["claims_core", "vendors"]);
    expect(models[0]).toMatchObject({ id: "sm-1", urn: "wr:t-42:semantic:model/sm-1", dimensions: [], measures: [] });
    // Only ONE downstream call for the whole list — no per-item definition fetch.
    expect(requests.filter((r) => r.path.endsWith("/definition")).length).toBe(0);
    // workspace filter forwarded as the aliased query param + JWT present.
    const list = requests.find((r) => r.path === "/api/v1/models");
    expect(list?.search.get("filter[workspace_id]")).toBe("ws-9");
    expect(list?.headers["authorization"]).toMatch(/^Bearer /);
  });

  it("semanticModel(name) hydrates published dimensions + measures (snake→camel)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semantic();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ semanticModel(name: "claims_core") { id name dimensions { name entity dimType } measures { name agg entity } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const m = (body?.data as any).semanticModel;
    expect(m.id).toBe("sm-1");
    expect(m.dimensions).toEqual([
      { name: "claim_type", entity: "claim", dimType: "categorical" },
      { name: "vendor", entity: "claim", dimType: "categorical" },
    ]);
    expect(m.measures).toEqual([{ name: "claim_count", agg: "count", entity: "claim" }]);
  });

  it("semanticModel(name) returns empty dims/measures for an unpublished model (409 swallowed)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semantic();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ semanticModel(name: "vendors") { id name dimensions { name } measures { name } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).semanticModel).toEqual({ id: "sm-2", name: "vendors", dimensions: [], measures: [] });
  });

  it("semanticModel(name) returns null for an unknown model name", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semantic();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ semanticModel(name: "does_not_exist") { id } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).semanticModel).toBeNull();
  });
});

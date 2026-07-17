import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/**
 * N+1 regression gate (BFF-FR-030 / AC-1 style).
 *
 * A multi-parent nested query — a page of experiments, each with its runs, each
 * run with its model — must produce O(1) downstream calls PER NESTED TYPE, not
 * O(N). Before the fix, Experiment.runs fired once per experiment and Run.model
 * once per run. This asserts exactly one /runs call and one /models call
 * regardless of how many experiments/runs come back.
 */
function downstream() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/experiments") {
      return {
        status: 200,
        body: {
          data: [
            { id: "e1", name: "exp-1" },
            { id: "e2", name: "exp-2" },
            { id: "e3", name: "exp-3" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/runs") {
      // Batched by filter[experiment_id]=e1,e2,e3 — one call for all experiments.
      return {
        status: 200,
        body: {
          data: [
            { id: "r1", experiment_id: "e1", name: "run-1", status: "succeeded", model_id: "m1" },
            { id: "r2", experiment_id: "e2", name: "run-2", status: "succeeded", model_id: "m2" },
            { id: "r3", experiment_id: "e3", name: "run-3", status: "succeeded", model_id: "m1" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/models") {
      // Batched by filter[id]=m1,m2 — one call for all runs' models (deduped).
      return {
        status: 200,
        body: {
          data: [
            { id: "m1", name: "model-1", stage: "production" },
            { id: "m2", name: "model-2", stage: "staging" },
          ],
          page: { has_more: false },
        },
      };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("N+1 protection on nested list resolvers (BFF-FR-030)", () => {
  it("makes ONE /runs and ONE /models call for 3 experiments x 3 runs", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      {
        query: `{
          experiments(first: 50) {
            nodes { id runs { nodes { id status model { id name stage } } } }
          }
        }`,
      },
      { contextValue: ctx },
    );

    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();

    const data: any = body?.data;
    const exps: any[] = data?.experiments?.nodes ?? [];
    expect(exps).toHaveLength(3);
    // Runs correctly grouped back to their experiment, models hydrated.
    const e1 = exps.find((e) => e.id === "e1");
    expect(e1.runs.nodes[0].id).toBe("r1");
    expect(e1.runs.nodes[0].model.name).toBe("model-1");

    // The crux: O(1) per nested type, not O(N).
    expect(requests.filter((r) => r.path === "/api/v1/runs")).toHaveLength(1);
    expect(requests.filter((r) => r.path === "/api/v1/models")).toHaveLength(1);
    // And the batch calls carried the joined id lists.
    const runsCall = requests.find((r) => r.path === "/api/v1/runs")!;
    expect(runsCall.search.get("filter[experiment_id]")).toBe("e1,e2,e3");
    const modelsCall = requests.find((r) => r.path === "/api/v1/models")!;
    expect(modelsCall.search.get("filter[id]")).toBe("m1,m2"); // deduped (r1,r3 share m1)
  });
});

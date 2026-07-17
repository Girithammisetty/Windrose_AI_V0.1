import { describe, it, expect } from "vitest";
import { loadConfig } from "../../src/config.js";
import { buildClients } from "../../src/clients/index.js";
import { buildLoaders } from "../../src/loaders/index.js";
import { mockFetch } from "../helpers/mockFetch.js";

const cfg = loadConfig();

describe("dataloaders (BFF-FR-030/031, BR-5)", () => {
  it("batches N ids into ONE downstream filter[id] call and isolates misses", async () => {
    const { fetchImpl, requests } = mockFetch((req) => {
      if (req.path === "/api/v1/datasets") {
        const ids = (req.search.get("filter[id]") ?? "").split(",");
        // Return only 'a' and 'c'; 'b' is absent -> should resolve to null.
        const data = ids
          .filter((id) => id !== "b")
          .map((id) => ({ id, name: `ds-${id}` }));
        return { status: 200, body: { data, page: { has_more: false } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });

    const clients = buildClients(cfg, { authorization: "Bearer t" }, fetchImpl);
    const loaders = buildLoaders(clients);

    const [a, b, c] = await Promise.all([
      loaders.datasetById.load("a"),
      loaders.datasetById.load("b"),
      loaders.datasetById.load("c"),
    ]);

    expect(a?.name).toBe("ds-a");
    expect(b).toBeNull(); // per-item error isolation
    expect(c?.name).toBe("ds-c");
    // Crucially: one batched call, not three (N+1 protection).
    const datasetCalls = requests.filter((r) => r.path === "/api/v1/datasets");
    expect(datasetCalls).toHaveLength(1);
    expect(datasetCalls[0]!.search.get("filter[id]")).toBe("a,b,c");
  });

  it("evalSuiteByKey dedups a suite shared across a page of runs (EvalRun N+1)", async () => {
    const { fetchImpl, requests } = mockFetch((req) => {
      if (req.path.startsWith("/api/v1/suites/")) {
        return { status: 200, body: { data: { id: "s1", name: "suite-1", cases: [] } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const clients = buildClients(cfg, { authorization: "Bearer t" }, fetchImpl);
    const loaders = buildLoaders(clients);

    // 5 runs all pinned to the same suite -> the loader must collapse to ONE fetch.
    const results = await Promise.all(
      Array.from({ length: 5 }, () => loaders.evalSuiteByKey.load("s1@")),
    );
    expect(results.every((r) => r?.id === "s1")).toBe(true);
    const suiteCalls = requests.filter((r) => r.path.startsWith("/api/v1/suites/"));
    expect(suiteCalls).toHaveLength(1); // not 5
  });
});

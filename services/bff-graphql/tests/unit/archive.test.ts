/**
 * Archive/restore for Dataset (dataset-service) and Experiment (experiment-service).
 * Dashboard archive/restore is covered in charts.test.ts. Response shapes mirror
 * the real downstream route bodies read from the Python handlers.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function downstream() {
  return mockFetch((req: CapturedRequest) => {
    // --- dataset-service: archive (DELETE, 200 not 204) + restore -------------
    if (req.path === "/api/v1/datasets/ds-1" && req.method === "DELETE") {
      return { status: 200, body: { data: { id: "ds-1", deleted: true, consumers: { downstream_edges: 0 } } } };
    }
    if (req.path === "/api/v1/datasets/ds-2/restore" && req.method === "POST") {
      return {
        status: 200,
        body: { data: { id: "ds-2", name: "Claims (restored)", description: "", status: "ready",
          tags: [], deleted_at: null, created_at: "2026-01-01T00:00:00Z" } },
      };
    }
    // --- dataset-service: edit name/description (PATCH) ------------------------
    if (req.path === "/api/v1/datasets/ds-3" && req.method === "PATCH") {
      const b = (req.body ?? {}) as { name?: string; description?: string };
      return {
        status: 200,
        body: { data: { id: "ds-3", name: b.name ?? "Old name",
          description: b.description ?? null, status: "ready", tags: [],
          deleted_at: null, created_at: "2026-01-01T00:00:00Z" } },
      };
    }
    // --- experiment-service: list_archived + archive (DELETE) + restore -------
    if (req.path === "/api/v1/experiments/list_archived" && req.method === "GET") {
      return {
        status: 200,
        body: { data: [{ id: "exp-old", name: "Old model", description: "", archived: true }], page: { has_more: false } },
      };
    }
    if (req.path === "/api/v1/experiments/exp-1" && req.method === "DELETE") {
      return { status: 200, body: { data: { id: "exp-1", name: "Claims triage", description: "", archived: true } } };
    }
    if (req.path === "/api/v1/experiments/exp-old/restore" && req.method === "PATCH") {
      return { status: 200, body: { data: { id: "exp-old", name: "Old model", description: "", archived: false } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = downstream();
  const ctx = await makeTestContext(fetchImpl);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("dataset archive/restore", () => {
  it("archiveDataset DELETEs and returns the deleted flag", async () => {
    const { body, requests } = await run(`mutation { archiveDataset(id: "ds-1") }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).archiveDataset).toBe(true);
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/datasets/ds-1")).toBe(true);
  });

  it("archiveDataset(force: true) passes ?force=true", async () => {
    const { requests } = await run(`mutation { archiveDataset(id: "ds-1", force: true) }`);
    const del = requests.find((r) => r.method === "DELETE" && r.path === "/api/v1/datasets/ds-1");
    expect(del?.search.get("force")).toBe("true");
  });

  it("updateDataset PATCHes name + description and maps the result", async () => {
    const { body, requests } = await run(
      `mutation { updateDataset(id: "ds-3", input: { name: "Auto Claims", description: "first-party" }) { id name description } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateDataset).toMatchObject({
      id: "ds-3", name: "Auto Claims", description: "first-party",
    });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/datasets/ds-3");
    expect(patch?.body).toEqual({ name: "Auto Claims", description: "first-party" });
  });

  it("updateDataset with description only omits name from the PATCH body", async () => {
    const { requests } = await run(
      `mutation { updateDataset(id: "ds-3", input: { description: "desc only" }) { id description } }`,
    );
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/datasets/ds-3");
    expect(patch?.body).toEqual({ description: "desc only" });
  });

  it("restoreDataset POSTs /restore and maps the (possibly renamed) dataset", async () => {
    const { body, requests } = await run(`mutation { restoreDataset(id: "ds-2") { id name archived archivedAt } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).restoreDataset).toMatchObject({
      id: "ds-2", name: "Claims (restored)", archived: false, archivedAt: null,
    });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/datasets/ds-2/restore")).toBe(true);
  });
});

describe("experiment archive/restore", () => {
  it("archivedExperiments lists via the dedicated list_archived route", async () => {
    const { body, requests } = await run(`{ archivedExperiments { nodes { id name archived } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).archivedExperiments.nodes).toEqual([{ id: "exp-old", name: "Old model", archived: true }]);
    expect(requests.some((r) => r.method === "GET" && r.path === "/api/v1/experiments/list_archived")).toBe(true);
  });

  it("archiveExperiment DELETEs and restoreExperiment PATCHes /restore", async () => {
    const archive = await run(`mutation { archiveExperiment(id: "exp-1") { id archived } }`);
    expect(archive.body?.errors).toBeUndefined();
    expect((archive.body?.data as any).archiveExperiment).toMatchObject({ id: "exp-1", archived: true });
    expect(archive.requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/experiments/exp-1")).toBe(true);

    const restore = await run(`mutation { restoreExperiment(id: "exp-old") { id archived } }`);
    expect(restore.body?.errors).toBeUndefined();
    expect((restore.body?.data as any).restoreExperiment).toMatchObject({ id: "exp-old", archived: false });
    expect(restore.requests.some((r) => r.method === "PATCH" && r.path === "/api/v1/experiments/exp-old/restore")).toBe(true);
  });
});

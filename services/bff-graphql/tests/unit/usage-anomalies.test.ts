/**
 * Usage anomaly detection review. Response shapes mirror the real downstream
 * route bodies — see services/usage-service/internal/api/handlers_anomalies.go.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function usage() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/anomalies" && req.method === "GET") {
      const status = req.search.get("status");
      const rows = [
        { id: "a-1", tenant_id: "t-42", meter_key: "tokens", day: "2026-07-10", observed: 500, mean: 100,
          stddev: 20, z: 20, status: "open", created_at: "2026-07-10T00:00:00Z" },
        { id: "a-2", tenant_id: "t-42", meter_key: "api_calls", day: "2026-07-09", observed: 90, mean: 80,
          stddev: 5, z: 2, status: "dismissed", dismissed_by: "u-1", created_at: "2026-07-09T00:00:00Z" },
      ];
      const data = status ? rows.filter((r) => r.status === status) : rows;
      return { status: 200, body: { data, page: { has_more: false } } };
    }
    if (req.path === "/api/v1/anomalies/a-1/dismiss" && req.method === "POST") {
      return { status: 200, body: { data: { id: "a-1", status: "dismissed" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = usage();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("usage: anomaly detection review", () => {
  it("lists all anomalies when no status filter is given", async () => {
    const { body } = await run(`{ anomalies { id meterKey status z } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).anomalies).toHaveLength(2);
  });

  it("filters anomalies by status=open", async () => {
    const { body, requests } = await run(`{ anomalies(status: "open") { id meterKey status } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).anomalies).toEqual([{ id: "a-1", meterKey: "tokens", status: "open" }]);
    const get = requests.find((r) => r.method === "GET" && r.path === "/api/v1/anomalies");
    expect(get?.search.get("status")).toBe("open");
  });

  it("dismissAnomaly POSTs the id and re-reads the full row", async () => {
    const { body, requests } = await run(`mutation { dismissAnomaly(id: "a-1") { id status meterKey z } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).dismissAnomaly).toMatchObject({ id: "a-1", meterKey: "tokens" });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/anomalies/a-1/dismiss")).toBe(true);
  });
});

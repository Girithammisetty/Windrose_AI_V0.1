/**
 * Audit compliance packs + chain-integrity verify. Response shapes mirror the
 * real downstream route bodies (NO envelope wrapper, unlike most other
 * audit-service/usage-service routes) — see
 * services/audit-service/internal/api/handlers.go.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function audit() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/audit/verify" && req.method === "POST") {
      expect(req.body).toMatchObject({ date: "2026-07-01" });
      return {
        status: 200,
        body: { valid: true, events_checked: 128, chain_head: "abc123", manifest_match: true, sealed: true },
      };
    }
    if (req.path === "/api/v1/compliance/soc2-pack" && req.method === "POST") {
      expect(req.body).toMatchObject({ from: "2026-06-01T00:00:00Z", to: "2026-07-01T00:00:00Z" });
      return { status: 202, body: { operation_id: "op-1", status: "running" } };
    }
    if (req.path === "/api/v1/compliance/ai-decision-log" && req.method === "POST") {
      return { status: 202, body: { operation_id: "op-2", status: "running" } };
    }
    if (req.path === "/api/v1/operations/op-1" && req.method === "GET") {
      return { status: 200, body: { operation_id: "op-1", status: "succeeded", result_url: "https://example/pack.zip" } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = audit();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("audit: chain-integrity verify", () => {
  it("verifyChainIntegrity posts the date and maps the real result", async () => {
    const { body } = await run(
      `mutation { verifyChainIntegrity(date: "2026-07-01") { valid eventsChecked chainHead manifestMatch sealed firstMismatchSeq } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).verifyChainIntegrity).toMatchObject({
      valid: true, eventsChecked: 128, chainHead: "abc123", manifestMatch: true, sealed: true, firstMismatchSeq: null,
    });
  });
});

describe("audit: compliance packs", () => {
  it("generateSoc2Pack POSTs the real RFC3339 range and returns the async job", async () => {
    const { body } = await run(
      `mutation($f:DateTime!,$t:DateTime!){ generateSoc2Pack(from: $f, to: $t) { operationId status resultUrl } }`,
      { f: "2026-06-01T00:00:00Z", t: "2026-07-01T00:00:00Z" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).generateSoc2Pack).toMatchObject({ operationId: "op-1", status: "running", resultUrl: null });
  });

  it("complianceOperation polls the real job and surfaces the download link once succeeded", async () => {
    const { body } = await run(`{ complianceOperation(id: "op-1") { operationId status resultUrl } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).complianceOperation).toMatchObject({
      operationId: "op-1", status: "succeeded", resultUrl: "https://example/pack.zip",
    });
  });
});

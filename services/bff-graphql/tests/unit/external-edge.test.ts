/**
 * BRD 60 WS3 — public governed edge. The edge verifies the caller is a live
 * agent principal, then forwards verbatim to the internal ingress. These tests
 * drive `handleExternalEdge` directly with fake req/res and a boundary-double
 * fetch, asserting auth gating, correct upstream targeting, header/body
 * passthrough, and verbatim response copy (the edge never rewrites the
 * governed decision).
 */
import { describe, it, expect } from "vitest";
import { handleExternalEdge, isExternalEdgePath } from "../../src/external/edge.js";
import { fakeJwt, testConfig } from "../helpers/context.js";
import { mockFetch } from "../helpers/mockFetch.js";

const cfg = testConfig();

function fakeReq(method: string, headers: Record<string, string>) {
  return { method, headers } as any;
}

function fakeRes() {
  return {
    statusCode: 0,
    headersOut: {} as Record<string, unknown>,
    body: "",
    writeHead(status: number, headers?: Record<string, unknown>) {
      this.statusCode = status;
      if (headers) this.headersOut = headers;
    },
    end(b?: string) {
      this.body = b ?? "";
    },
  };
}

function agentAuth(extra: Record<string, unknown> = {}) {
  return `Bearer ${fakeJwt({ sub: "agent:acme-ext-bot@1", tenant_id: "t-1", typ: "agent_autonomous", agent_id: "acme-ext-bot", scopes: ["case.apply_disposition"], ...extra })}`;
}

describe("external governed edge (BRD 60 WS3)", () => {
  it("recognizes the two edge paths and nothing else", () => {
    expect(isExternalEdgePath("/external/v1/intents")).toBe(true);
    expect(isExternalEdgePath("/external/v1/mcp")).toBe(true);
    expect(isExternalEdgePath("/graphql")).toBe(false);
    expect(isExternalEdgePath("/external/v1/other")).toBe(false);
  });

  it("rejects a missing token with 401 and never forwards", async () => {
    const { fetchImpl, requests } = mockFetch(() => ({ status: 200, body: {} }));
    const res = fakeRes();
    await handleExternalEdge(fakeReq("POST", {}), res as any, "/external/v1/intents", "{}", { cfg, jwks: undefined, fetchImpl });
    expect(res.statusCode).toBe(401);
    expect(requests).toHaveLength(0);
  });

  it("rejects a non-agent (user) token with 403 and never forwards", async () => {
    const { fetchImpl, requests } = mockFetch(() => ({ status: 200, body: {} }));
    const res = fakeRes();
    const auth = `Bearer ${fakeJwt({ sub: "u-1", tenant_id: "t-1", typ: "user", scopes: ["*"] })}`;
    await handleExternalEdge(fakeReq("POST", { authorization: auth }), res as any, "/external/v1/intents", "{}", { cfg, jwks: undefined, fetchImpl });
    expect(res.statusCode).toBe(403);
    expect(requests).toHaveLength(0);
  });

  it("rejects non-POST with 405", async () => {
    const { fetchImpl, requests } = mockFetch(() => ({ status: 200, body: {} }));
    const res = fakeRes();
    await handleExternalEdge(fakeReq("GET", { authorization: agentAuth() }), res as any, "/external/v1/intents", "", { cfg, jwks: undefined, fetchImpl });
    expect(res.statusCode).toBe(405);
    expect(requests).toHaveLength(0);
  });

  it("forwards propose to agent-runtime /external/v1/intents with the Bearer token + body, verbatim response", async () => {
    const pending = { data: { id: "prop-1", status: "pending", predicted_effect: { risk: "low" } } };
    const { fetchImpl, requests } = mockFetch((r) => {
      expect(r.path).toBe("/external/v1/intents");
      expect(r.headers["authorization"]).toBe(agentAuth());
      expect(r.body).toMatchObject({ tool_id: "case.apply_disposition", tier: "write-proposal" });
      return { status: 200, body: pending };
    });
    const res = fakeRes();
    const body = JSON.stringify({ tool_id: "case.apply_disposition", tool_version: "1.0.0", tier: "write-proposal", args: {}, affected_urns: ["urn:x"] });
    await handleExternalEdge(fakeReq("POST", { authorization: agentAuth(), traceparent: "00-abc-def-01" }), res as any, "/external/v1/intents", body, { cfg, jwks: undefined, fetchImpl });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toEqual(pending);
    // The customer's trace propagates to the internal ingress.
    expect(requests[0]!.headers["traceparent"]).toBe("00-abc-def-01");
    // Forwarded to the agent-runtime host from config, not the mcp-gateway.
    expect(requests[0]!.url.startsWith(cfg.services.agentRuntime)).toBe(true);
  });

  it("forwards list-tools to the mcp-gateway /mcp (JSON-RPC passthrough)", async () => {
    const toolsResult = { jsonrpc: "2.0", id: 1, result: { tools: [{ name: "case.apply_disposition" }] } };
    const { fetchImpl, requests } = mockFetch((r) => {
      expect(r.path).toBe("/mcp");
      expect(r.body).toMatchObject({ method: "tools/list" });
      return { status: 200, body: toolsResult };
    });
    const res = fakeRes();
    const rpc = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} });
    await handleExternalEdge(fakeReq("POST", { authorization: agentAuth() }), res as any, "/external/v1/mcp", rpc, { cfg, jwks: undefined, fetchImpl });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(res.body)).toEqual(toolsResult);
    expect(requests[0]!.url.startsWith(cfg.services.mcpGateway)).toBe(true);
  });

  it("copies an upstream governance refusal back verbatim (status + envelope)", async () => {
    const refusal = { error: { code: "GUARDRAIL_VIOLATION", message: "write-direct is not permitted for an external agent" } };
    const { fetchImpl } = mockFetch(() => ({ status: 403, body: refusal }));
    const res = fakeRes();
    const body = JSON.stringify({ tool_id: "case.apply_disposition", tool_version: "1.0.0", tier: "write-direct", args: {}, affected_urns: ["urn:x"] });
    await handleExternalEdge(fakeReq("POST", { authorization: agentAuth() }), res as any, "/external/v1/intents", body, { cfg, jwks: undefined, fetchImpl });
    expect(res.statusCode).toBe(403);
    expect(JSON.parse(res.body)).toEqual(refusal);
  });

  it("returns 502 when the internal ingress is unreachable (never masks as success)", async () => {
    const failing = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const res = fakeRes();
    await handleExternalEdge(fakeReq("POST", { authorization: agentAuth() }), res as any, "/external/v1/intents", "{}", { cfg, jwks: undefined, fetchImpl: failing });
    expect(res.statusCode).toBe(502);
    expect(JSON.parse(res.body).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });
});

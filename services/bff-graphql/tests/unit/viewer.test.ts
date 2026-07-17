import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest, type MockResponse } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** rbac /me/capabilities double, parametrised per persona. */
function rbac(caps: { roles: string[]; capabilities: string[]; admin: boolean }) {
  return mockFetch((req: CapturedRequest): MockResponse => {
    if (req.path === "/api/v1/me/capabilities") {
      return { status: 200, body: { roles: caps.roles, capabilities: caps.capabilities, admin: caps.admin } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const VIEWER = `{ me { userId roles capabilities } }`;

describe("Viewer.roles + capabilities (rbac passthrough, BFF makes no decision)", () => {
  it("resolves roles + capabilities from rbac /me/capabilities, forwarding the JWT", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = rbac({
      roles: ["Case Analyst"],
      capabilities: ["case.case.read", "ai.proposal.read", "chart.dashboard.read"],
      admin: false,
    });
    const ctx = await makeTestContext(fetchImpl, {
      sub: "user-adjuster",
      tenant_id: "t-42",
      typ: "user",
      scopes: [],
    });

    const res = await server.executeOperation({ query: VIEWER }, { contextValue: ctx });
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const me: any = body?.data?.me;
    expect(me.roles).toEqual(["Case Analyst"]);
    expect(me.capabilities).toEqual(["case.case.read", "ai.proposal.read", "chart.dashboard.read"]);

    // Exactly one rbac call, carrying the caller's JWT verbatim (passthrough).
    const rbacCalls = requests.filter((r) => r.path === "/api/v1/me/capabilities");
    expect(rbacCalls).toHaveLength(1);
    expect(rbacCalls[0]!.headers["authorization"]).toMatch(/^Bearer /);
  });

  it("returns the '*' wildcard for an admin persona", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = rbac({ roles: ["Admin"], capabilities: ["*"], admin: true });
    const ctx = await makeTestContext(fetchImpl, { sub: "user-admin", tenant_id: "t-42", typ: "user", scopes: [] });

    const res = await server.executeOperation({ query: VIEWER }, { contextValue: ctx });
    const me: any = (res.body.kind === "single" ? res.body.singleResult : null)?.data?.me;
    expect(me.roles).toEqual(["Admin"]);
    expect(me.capabilities).toEqual(["*"]);
  });

  it("two personas get DIFFERENT capabilities (this is what fixes the all-admin bug)", async () => {
    const server = makeApolloServer(cfg);

    const ds = rbac({
      roles: ["Model Builder"],
      capabilities: ["dataset.dataset.list", "experiment.experiment.read"],
      admin: false,
    });
    const dsCtx = await makeTestContext(ds.fetchImpl, { sub: "user-ds", tenant_id: "t-42", typ: "user", scopes: [] });
    const dsRes = await server.executeOperation({ query: VIEWER }, { contextValue: dsCtx });
    const dsMe: any = (dsRes.body.kind === "single" ? dsRes.body.singleResult : null)?.data?.me;

    const adj = rbac({ roles: ["Case Analyst"], capabilities: ["case.case.read"], admin: false });
    const adjCtx = await makeTestContext(adj.fetchImpl, { sub: "user-adj", tenant_id: "t-42", typ: "user", scopes: [] });
    const adjRes = await server.executeOperation({ query: VIEWER }, { contextValue: adjCtx });
    const adjMe: any = (adjRes.body.kind === "single" ? adjRes.body.singleResult : null)?.data?.me;

    expect(dsMe.capabilities).not.toEqual(adjMe.capabilities);
    expect(dsMe.capabilities).toContain("experiment.experiment.read");
    expect(adjMe.capabilities).not.toContain("experiment.experiment.read");
  });

  it("fails SAFE to [] when rbac is unavailable (never over-exposes)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch(() => ({ status: 503, body: { error: { code: "UNAVAILABLE", message: "down" } } }));
    const ctx = await makeTestContext(fetchImpl, { sub: "user-x", tenant_id: "t-42", typ: "user", scopes: [] });

    const res = await server.executeOperation({ query: VIEWER }, { contextValue: ctx });
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const me: any = body?.data?.me;
    expect(me.roles).toEqual([]);
    expect(me.capabilities).toEqual([]);
  });
});

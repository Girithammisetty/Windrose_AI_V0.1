/**
 * rbac authz-explain debug tool ("why was I denied"). Response shape mirrors
 * the real downstream route — see
 * services/rbac-service/internal/api/handlers_authz.go handleAuthzExplain.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function rbac() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/authz/explain" && req.method === "POST") {
      expect(req.body).toMatchObject({ user_id: "u-9", action: "case.case.read" });
      return {
        status: 200,
        body: {
          allowed: true,
          reason: "role grant",
          chain: [
            { type: "membership", group: "g-1", group_type: "permission" },
            { type: "role", role: "Case Manager", action: "case.case.read", workspace_scoped: false },
          ],
        },
      };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = rbac();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("rbac: authz explain", () => {
  it("posts user_id + action and maps the real decision chain", async () => {
    const { body, requests } = await run(
      `query($input: ExplainAuthzInput!) {
        explainAuthz(input: $input) {
          allowed reason
          chain { type group groupType role action workspaceScoped }
        }
      }`,
      { input: { userId: "u-9", action: "case.case.read" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).explainAuthz).toMatchObject({
      allowed: true,
      reason: "role grant",
      chain: [
        { type: "membership", group: "g-1", groupType: "permission" },
        { type: "role", role: "Case Manager", action: "case.case.read", workspaceScoped: false },
      ],
    });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/authz/explain");
    expect(post?.body).toMatchObject({ user_id: "u-9", action: "case.case.read" });
  });
});

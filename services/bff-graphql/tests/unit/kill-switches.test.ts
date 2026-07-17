/**
 * Kill switches (agent-runtime registry + tool-plane admin plane, Tier-1
 * safety control). Response shapes mirror the real downstream route bodies —
 * see services/agent-runtime/app/api/routes/registry.py (kill-switches) and
 * services/tool-plane/internal/api/handlers_admin.go.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function killSwitches() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/registry/kill-switches" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { kill_id: "k-1", scope: "agent_version_tenant", agent_key: "case-triage", version: null,
              tenant_id: "t-42", active: true, reason: "INC-1", set_by: "user:u-1",
              created_at: "2026-07-12T00:00:00Z" },
          ],
        },
      };
    }
    if (req.path === "/api/v1/registry/kill-switches" && req.method === "POST") {
      return { status: 200, body: { data: { kill_id: "k-new", active: true } } };
    }
    if (req.path === "/api/v1/registry/kill-switches/k-new" && req.method === "DELETE") {
      return { status: 200, body: { data: { kill_id: "k-new", active: false } } };
    }
    if (req.path === "/api/v1/kill-switches" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "tk-1", scope: "tool_version", tool_id: "pipeline.launch_run", version: "1.0.0",
              tenant_id: null, active: true, reason: "TPL-INC-1", set_by: "user:u-1",
              created_at: "2026-07-12T00:00:00Z" },
          ],
        },
      };
    }
    if (req.path === "/api/v1/kill-switches" && req.method === "POST") {
      return {
        status: 201,
        body: { data: { id: "tk-new", active: true, set_by: "user:u-1" } },
      };
    }
    if (req.path === "/api/v1/kill-switches/tk-new" && req.method === "DELETE") {
      return { status: 200, body: { data: { id: "tk-new", active: false } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = killSwitches();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("kill switches: agent-runtime", () => {
  it("lists active agent kill switches", async () => {
    const { body } = await run(
      `{ agentKillSwitches { id target scope agentKey tenantId active reason setBy } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).agentKillSwitches[0]).toMatchObject({
      id: "k-1", target: "AGENT", scope: "agent_version_tenant", agentKey: "case-triage",
      tenantId: "t-42", active: true, reason: "INC-1", setBy: "user:u-1",
    });
  });

  it("createAgentKillSwitch POSTs the reason + agent_key and re-reads the list for the full row", async () => {
    const { body, requests } = await run(
      `mutation { createAgentKillSwitch(agentKey: "case-triage", reason: "INC-2") { id scope agentKey reason active } }`,
    );
    expect(body?.errors).toBeUndefined();
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/registry/kill-switches");
    expect(post?.body).toMatchObject({ agent_key: "case-triage", reason: "INC-2" });
    // the mock's list handler is static (always returns k-1) — the resolver's
    // re-read-the-list step falls back to echoing the accepted input verbatim.
    expect((body?.data as any).createAgentKillSwitch).toMatchObject({
      scope: "agent_version_tenant", agentKey: "case-triage", reason: "INC-2", active: true,
    });
  });

  it("deleteAgentKillSwitch DELETEs by id and returns {id, active: false}", async () => {
    const { body, requests } = await run(
      `mutation { deleteAgentKillSwitch(killId: "k-new") { id active } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteAgentKillSwitch).toMatchObject({ id: "k-new", active: false });
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/registry/kill-switches/k-new")).toBe(true);
  });
});

describe("kill switches: tool-plane", () => {
  it("lists active tool kill switches", async () => {
    const { body } = await run(
      `{ toolKillSwitches { id target scope toolId version tenantId active reason setBy } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).toolKillSwitches[0]).toMatchObject({
      id: "tk-1", target: "TOOL", scope: "tool_version", toolId: "pipeline.launch_run",
      version: "1.0.0", tenantId: null, active: true, reason: "TPL-INC-1", setBy: "user:u-1",
    });
  });

  it("createToolKillSwitch POSTs the tool_id/scope/reason and maps the 201 response", async () => {
    const { body, requests } = await run(
      `mutation { createToolKillSwitch(toolId: "pipeline.launch_run", scope: "tool", reason: "TPL-INC-2") { id scope toolId reason active setBy } }`,
    );
    expect(body?.errors).toBeUndefined();
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/kill-switches");
    expect(post?.body).toMatchObject({ tool_id: "pipeline.launch_run", scope: "tool", reason: "TPL-INC-2" });
    expect((body?.data as any).createToolKillSwitch).toMatchObject({
      id: "tk-new", scope: "tool", toolId: "pipeline.launch_run", reason: "TPL-INC-2",
      active: true, setBy: "user:u-1",
    });
  });

  it("deleteToolKillSwitch DELETEs by id and returns {id, active: false}", async () => {
    const { body, requests } = await run(
      `mutation { deleteToolKillSwitch(id: "tk-new") { id active } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteToolKillSwitch).toMatchObject({ id: "tk-new", active: false });
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/kill-switches/tk-new")).toBe(true);
  });
});

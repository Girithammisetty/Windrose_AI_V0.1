import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** Boundary double covering the admin surfaces across identity + rbac + audit.
 * Every response shape mirrors the REAL downstream route bodies (read from the
 * Go handler/DTO structs), so the snake→camel mapping asserts against the true
 * field names — not assumptions. */
function admin() {
  return mockFetch((req: CapturedRequest) => {
    // --- identity: user directory (cursor-paginated {data,page}) --------------
    if (req.path === "/api/v1/users" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "u-1", tenant_id: "t-42", email: "ada@demo", full_name: "Ada L",
              status: "active", last_login_at: "2026-07-10T00:00:00Z", created_at: "2026-01-01T00:00:00Z" },
            { id: "u-2", tenant_id: "t-42", email: "bob@demo", full_name: "Bob",
              status: "invited", created_at: "2026-02-01T00:00:00Z" },
          ],
          page: { next_cursor: "cur-u", has_more: true },
        },
      };
    }
    // --- identity: invite user (201 bare User) --------------------------------
    if (req.path === "/api/v1/users/invite" && req.method === "POST") {
      return {
        status: 201,
        body: { id: "u-new", tenant_id: "t-42", email: req.body.email,
          full_name: req.body.full_name ?? "", status: "invited", created_at: "2026-07-11T00:00:00Z" },
      };
    }
    // --- identity: service accounts -------------------------------------------
    if (req.path === "/api/v1/service-accounts" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "sa-1", tenant_id: "t-42", name: "etl-bot", scopes: ["dataset.dataset.read"],
              last_used_at: "2026-07-09T00:00:00Z", created_at: "2026-01-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    // --- identity: tenant (bare Tenant object) --------------------------------
    if (req.path === "/api/v1/tenants/t-42" && req.method === "GET") {
      return {
        status: 200,
        body: { id: "t-42", name: "demo", display_name: "Demo Co", owner_email: "root@demo",
          tier: "pool", cloud: "aws", status: "active", subdomain: "demo", platform_version: "1.2.3",
          auto_upgrade: true, modules: ["cases", "ml"],
          quotas: { cpu: 8, memory: "16Gi", processing_cpu: 4, processing_memory: "8Gi" },
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z" },
      };
    }
    // --- rbac: workspaces list -------------------------------------------------
    if (req.path === "/api/v1/workspaces" && req.method === "GET") {
      const archivedOnly = req.search.get("archived") === "only";
      return {
        status: 200,
        body: {
          data: archivedOnly
            ? [{ id: "ws-old", tenant_id: "t-42", name: "Retired", description: "",
                 public: false, created_by: "u-1", archived_at: "2026-06-01T00:00:00Z",
                 created_at: "2026-01-01T00:00:00Z", updated_at: "2026-06-01T00:00:00Z" }]
            : [{ id: "ws-1", tenant_id: "t-42", name: "Claims", description: "Claims workspace",
                 public: true, created_by: "u-1", created_at: "2026-01-01T00:00:00Z",
                 updated_at: "2026-02-01T00:00:00Z" }],
          page: { has_more: false },
        },
      };
    }
    // --- rbac: create workspace (201 bare Workspace) ---------------------------
    if (req.path === "/api/v1/workspaces" && req.method === "POST") {
      return {
        status: 201,
        body: { id: "ws-new", tenant_id: "t-42", name: req.body.name,
          description: req.body.description ?? "", public: req.body.public ?? false,
          created_by: "u-1", created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" },
      };
    }
    // --- rbac: groups + members -----------------------------------------------
    if (req.path === "/api/v1/groups" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "g-1", tenant_id: "t-42", name: "Adjusters", description: "Front line",
              group_type: "permission", system: false, auto_generated: false,
              created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/groups/g-1/members" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { group_id: "g-1", user_id: "u-1", created_at: "2026-01-01T00:00:00Z" },
            { group_id: "g-1", user_id: "u-2", expires_at: "2026-12-01T00:00:00Z",
              created_at: "2026-02-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/groups/g-1/members/u-9" && req.method === "PUT") {
      return { status: 201, body: { group_id: "g-1", user_id: "u-9", created: true } };
    }
    if (req.path === "/api/v1/groups/g-1/members/u-9" && req.method === "DELETE") {
      return { status: 204 };
    }
    // --- rbac: team CRUD (groups POST/PATCH/DELETE + role binding) ------------
    if (req.path === "/api/v1/groups" && req.method === "POST") {
      return {
        status: 201,
        body: { id: "g-new", tenant_id: "t-42", name: req.body.name, description: req.body.description ?? "",
          group_type: req.body.group_type ?? "permission", system: false, auto_generated: false,
          created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/groups/g-1" && req.method === "PATCH") {
      return {
        status: 200,
        body: { id: "g-1", tenant_id: "t-42", name: req.body.name ?? "Adjusters",
          description: req.body.description ?? "Front line", group_type: "permission",
          system: false, auto_generated: false,
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/groups/g-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    if (req.path === "/api/v1/groups/g-1/roles/r-1" && req.method === "PUT") {
      return { status: 200, body: { group_id: "g-1", role_id: "r-1", status: "bound" } };
    }
    if (req.path === "/api/v1/groups/g-1/roles/r-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    if (req.path === "/api/v1/roles" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "r-1", tenant_id: null, name: "Adjuster", system: true, version: 1,
              actions: ["case.case.read"], created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    // --- audit: search ({data,page}, nested actor / via_agent) ----------------
    if (req.path === "/api/v1/audit/search" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { event_id: "ev-1", event_type: "dataset.created", tenant_id: "t-42",
              actor: { type: "user", id: "u-1" }, resource_urn: "wr:t-42:dataset:dataset/d1",
              action: "dataset.dataset.create", occurred_at: "2026-07-10T10:00:00Z",
              ingested_at: "2026-07-10T10:00:01Z", trace_id: "tr-1",
              payload_digest: "abc", body_withheld: false, payload: { rows: 10 },
              chain_seq: 42, chain_hash: "hash-1" },
            { event_id: "ev-2", event_type: "agent_run", tenant_id: "t-42",
              actor: { type: "agent", id: "triage-bot" },
              via_agent: { agent_id: "triage-bot", version: "1.0" },
              action: "ai.agent.run", occurred_at: "2026-07-10T09:00:00Z",
              body_withheld: true },
          ],
          page: { next_cursor: "cur-a", has_more: true },
        },
      };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = admin();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("admin: identity user directory (JWT forwarded, snake→camel)", () => {
  it("lists users with lastLoginAt/status and propagates the cursor", async () => {
    const { body, requests } = await run(
      `{ users(first: 2) { nodes { id email fullName status lastLoginAt createdAt urn } pageInfo { nextCursor hasMore } } }`,
    );
    expect(body?.errors).toBeUndefined();
    const conn: any = (body?.data as any).users;
    expect(conn.nodes.map((u: any) => u.email)).toEqual(["ada@demo", "bob@demo"]);
    expect(conn.nodes[0]).toMatchObject({ status: "active", lastLoginAt: "2026-07-10T00:00:00Z",
      urn: "wr:t-42:identity:user/u-1" });
    expect(conn.pageInfo).toEqual({ nextCursor: "cur-u", hasMore: true });
    expect(requests[0]?.headers["authorization"]).toMatch(/^Bearer /);
    expect(requests[0]?.search.get("limit")).toBe("2");
  });

  it("inviteUser POSTs to /users/invite and maps the invited user", async () => {
    const { body, requests } = await run(
      `mutation($input: InviteUserInput!, $k: String!) { inviteUser(input: $input, idempotencyKey: $k) { id email status } }`,
      { input: { email: "new@demo", fullName: "New Hire" }, k: "idem-inv" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).inviteUser).toMatchObject({ id: "u-new", email: "new@demo", status: "invited" });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/users/invite");
    expect(post?.body).toMatchObject({ email: "new@demo", full_name: "New Hire" });
    expect(post?.headers["idempotency-key"]).toBe("idem-inv");
  });

  it("surfaces a downstream 5xx on the invite (Keycloak) path honestly (no fake success)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) =>
      req.path === "/api/v1/users/invite"
        ? { status: 502, body: { error: { code: "UNAVAILABLE", message: "keycloak down", trace_id: "t" } } }
        : { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } },
    );
    const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
    const res = await server.executeOperation(
      { query: `mutation($input: InviteUserInput!) { inviteUser(input: $input) { id } }`,
        variables: { input: { email: "x@demo" } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.data?.inviteUser ?? null).toBeNull();
    // The BFF surfaces the downstream failure (mapped to SERVICE_UNAVAILABLE for a
    // 5xx) rather than fabricating a success — END STATE honesty on the KC path.
    expect(body?.errors?.[0]?.extensions?.code).toBe("SERVICE_UNAVAILABLE");
  });
});

describe("admin: identity service accounts + tenant", () => {
  it("lists service accounts (secrets never surfaced; only metadata)", async () => {
    const { body } = await run(
      `{ serviceAccounts { nodes { id name scopes lastUsedAt urn } } }`,
    );
    expect(body?.errors).toBeUndefined();
    const sa = (body?.data as any).serviceAccounts.nodes[0];
    expect(sa).toMatchObject({ id: "sa-1", name: "etl-bot", scopes: ["dataset.dataset.read"],
      urn: "wr:t-42:identity:service_account/sa-1" });
  });

  it("reads the tenant + settings with nested quotas (snake→camel)", async () => {
    const { body, requests } = await run(
      `{ tenant(id: "t-42") { id name displayName tier cloud status modules quotas { cpu processingCpu processingMemory } } }`,
    );
    expect(body?.errors).toBeUndefined();
    const t = (body?.data as any).tenant;
    expect(t).toMatchObject({ name: "demo", displayName: "Demo Co", tier: "pool", status: "active",
      modules: ["cases", "ml"] });
    expect(t.quotas).toMatchObject({ cpu: 8, processingCpu: 4, processingMemory: "8Gi" });
    expect(requests[0]?.path).toBe("/api/v1/tenants/t-42");
  });
});

describe("admin: rbac workspaces + groups", () => {
  it("lists workspaces and derives archived from archived_at", async () => {
    const { body } = await run(`{ workspaces { nodes { id name public archived archivedAt } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).workspaces.nodes[0]).toMatchObject({ id: "ws-1", name: "Claims",
      public: true, archived: false, archivedAt: null });
  });

  it("passes archived=only through and reports archived=true", async () => {
    const { body, requests } = await run(`{ workspaces(archived: "only") { nodes { id archived } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).workspaces.nodes[0]).toMatchObject({ id: "ws-old", archived: true });
    expect(requests[0]?.search.get("archived")).toBe("only");
  });

  it("createWorkspace POSTs the snake_case body and maps the result", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateWorkspaceInput!, $k: String!) { createWorkspace(input: $input, idempotencyKey: $k) { id name public } }`,
      { input: { name: "New WS", description: "d", public: true }, k: "idem-ws" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createWorkspace).toMatchObject({ id: "ws-new", name: "New WS", public: true });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/workspaces");
    expect(post?.body).toMatchObject({ name: "New WS", description: "d", public: true });
    expect(post?.headers["idempotency-key"]).toBe("idem-ws");
  });

  it("lists groups (group_type → groupType) and their members", async () => {
    const { body } = await run(
      `{ groups { nodes { id name groupType system } } groupMembers(groupId: "g-1") { userId expiresAt } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).groups.nodes[0]).toMatchObject({ id: "g-1", name: "Adjusters", groupType: "permission" });
    const members = (body?.data as any).groupMembers;
    expect(members.map((m: any) => m.userId)).toEqual(["u-1", "u-2"]);
    expect(members[1].expiresAt).toBe("2026-12-01T00:00:00Z");
  });

  it("addGroupMember PUTs by user id and removeGroupMember returns true on 204", async () => {
    const add = await run(
      `mutation { addGroupMember(groupId: "g-1", userId: "u-9", idempotencyKey: "idem-m") }`,
    );
    expect(add.body?.errors).toBeUndefined();
    expect((add.body?.data as any).addGroupMember).toBe(true);
    const put = add.requests.find((r) => r.method === "PUT" && r.path === "/api/v1/groups/g-1/members/u-9");
    expect(put?.headers["idempotency-key"]).toBe("idem-m");

    const rm = await run(`mutation { removeGroupMember(groupId: "g-1", userId: "u-9") }`);
    expect(rm.body?.errors).toBeUndefined();
    expect((rm.body?.data as any).removeGroupMember).toBe(true);
  });
});

describe("admin: teams (permission-type group CRUD + role binding)", () => {
  it("createTeam POSTs group_type=permission regardless of caller input", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateTeamInput!, $k: String) { createTeam(input: $input, idempotencyKey: $k) { id name groupType } }`,
      { input: { name: "Claims Ops", description: "front line" }, k: "idem-t" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createTeam).toMatchObject({ id: "g-new", name: "Claims Ops", groupType: "permission" });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/groups");
    expect(post?.body).toMatchObject({ name: "Claims Ops", description: "front line", group_type: "permission" });
    expect(post?.headers["idempotency-key"]).toBe("idem-t");
  });

  it("updateTeam PATCHes name/description and returns the mapped group", async () => {
    const { body, requests } = await run(
      `mutation($input: UpdateTeamInput!) { updateTeam(id: "g-1", input: $input) { id name } }`,
      { input: { name: "Adjusters Renamed" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateTeam).toMatchObject({ id: "g-1", name: "Adjusters Renamed" });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/groups/g-1");
    expect(patch?.body).toMatchObject({ name: "Adjusters Renamed" });
  });

  it("deleteTeam returns true on 204", async () => {
    const { body } = await run(`mutation { deleteTeam(id: "g-1") }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteTeam).toBe(true);
  });

  it("assignTeamRole PUTs the role binding and unassignTeamRole DELETEs it", async () => {
    const bind = await run(`mutation { assignTeamRole(groupId: "g-1", roleId: "r-1") }`);
    expect(bind.body?.errors).toBeUndefined();
    expect((bind.body?.data as any).assignTeamRole).toBe(true);
    expect(bind.requests.some((r) => r.method === "PUT" && r.path === "/api/v1/groups/g-1/roles/r-1")).toBe(true);

    const unbind = await run(`mutation { unassignTeamRole(groupId: "g-1", roleId: "r-1") }`);
    expect(unbind.body?.errors).toBeUndefined();
    expect((unbind.body?.data as any).unassignTeamRole).toBe(true);
    expect(unbind.requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/groups/g-1/roles/r-1")).toBe(true);
  });

  it("roles lists tenant + system roles for the role picker", async () => {
    const { body } = await run(`{ roles { nodes { id name system actions } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).roles.nodes[0]).toMatchObject({ id: "r-1", name: "Adjuster", system: true });
  });
});

describe("admin: audit trail search", () => {
  it("flattens nested actor/via_agent, passes filters, and paginates", async () => {
    const { body, requests } = await run(
      `query($from: DateTime, $to: DateTime) {
        auditEvents(from: $from, to: $to, eventType: "agent_run", first: 2) {
          nodes { eventId eventType actorType actorId viaAgentId action occurredAt bodyWithheld payload urn }
          pageInfo { nextCursor hasMore }
        }
      }`,
      { from: "2026-07-01T00:00:00Z", to: "2026-07-11T00:00:00Z" },
    );
    expect(body?.errors).toBeUndefined();
    const conn: any = (body?.data as any).auditEvents;
    expect(conn.nodes[0]).toMatchObject({ eventId: "ev-1", eventType: "dataset.created",
      actorType: "user", actorId: "u-1", urn: "wr:t-42:audit:event/ev-1" });
    expect(conn.nodes[0].payload).toEqual({ rows: 10 });
    expect(conn.nodes[1]).toMatchObject({ eventType: "agent_run", actorType: "agent",
      viaAgentId: "triage-bot", bodyWithheld: true, payload: null });
    expect(conn.pageInfo).toEqual({ nextCursor: "cur-a", hasMore: true });
    const q = requests[0]?.search;
    expect(q?.get("event_type")).toBe("agent_run");
    expect(q?.get("from")).toBe("2026-07-01T00:00:00Z");
    expect(q?.get("to")).toBe("2026-07-11T00:00:00Z");
  });

  it("defaults from/to to a 7-day window when omitted", async () => {
    const { body, requests } = await run(`{ auditEvents { nodes { eventId } } }`);
    expect(body?.errors).toBeUndefined();
    const q = requests[0]?.search;
    expect(q?.get("from")).toBeTruthy();
    expect(q?.get("to")).toBeTruthy();
    const span = new Date(q!.get("to")!).getTime() - new Date(q!.get("from")!).getTime();
    // ~7 days in ms (allow a little slack for execution time).
    expect(span).toBeGreaterThan(6.9 * 24 * 3600 * 1000);
    expect(span).toBeLessThan(7.1 * 24 * 3600 * 1000);
  });
});

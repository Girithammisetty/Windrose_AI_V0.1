import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** Boundary double for the Tier 4b identity/rbac admin surfaces. Every response
 * shape mirrors the REAL downstream route bodies (read from the Go handlers +
 * domain structs), so the snake→camel mapping asserts against the true field
 * names — not assumptions. */
function adminLifecycle() {
  return mockFetch((req: CapturedRequest) => {
    // --- identity: user lifecycle (bare User back; no {data} envelope) --------
    if (req.path === "/api/v1/users/u-1" && req.method === "PATCH") {
      return {
        status: 200,
        body: { id: "u-1", tenant_id: "t-42", email: "ada@demo", full_name: req.body.full_name,
          status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/users/u-1/deactivate" && req.method === "POST") {
      // The last-admin guard (BR-9): 409 unless ?override_last_admin=true.
      if (req.search.get("override_last_admin") !== "true") {
        return {
          status: 409,
          body: { error: { code: "LAST_ADMIN", message: "cannot deactivate the last tenant admin", trace_id: "t" } },
        };
      }
      return {
        status: 200,
        body: { id: "u-1", tenant_id: "t-42", email: "ada@demo", full_name: "Ada L",
          status: "deactivated", created_at: "2026-01-01T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/users/u-2/deactivate" && req.method === "POST") {
      return {
        status: 200,
        body: { id: "u-2", tenant_id: "t-42", email: "bob@demo", full_name: "Bob",
          status: "deactivated", created_at: "2026-02-01T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/users/u-2/invite/resend" && req.method === "POST") {
      return {
        status: 200,
        body: { id: "u-2", tenant_id: "t-42", email: "bob@demo", full_name: "Bob",
          status: "invited", created_at: "2026-02-01T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/users/u-2" && req.method === "DELETE") {
      return { status: 204 };
    }
    // --- identity: service accounts ({service_account, api_key} — key shown ONCE)
    if (req.path === "/api/v1/service-accounts" && req.method === "POST") {
      return {
        status: 201,
        body: {
          service_account: { id: "sa-new", tenant_id: "t-42", name: req.body.name,
            scopes: req.body.scopes ?? [], expires_at: req.body.expires_at ?? null,
            created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
          api_key: "wr_sa_sa-new.s3cr3t-once",
        },
      };
    }
    if (req.path === "/api/v1/service-accounts/sa-1/rotate" && req.method === "POST") {
      return {
        status: 200,
        body: {
          service_account: { id: "sa-1", tenant_id: "t-42", name: "etl-bot",
            scopes: ["dataset.dataset.read"], created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-07-12T00:00:00Z" },
          api_key: "wr_sa_sa-1.r0tated-once",
        },
      };
    }
    if (req.path === "/api/v1/service-accounts/sa-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    // --- rbac: workspace lifecycle (archived state IS archived_at) ------------
    if (req.path === "/api/v1/workspaces/ws-1" && req.method === "PATCH") {
      return {
        status: 200,
        body: { id: "ws-1", tenant_id: "t-42", name: req.body.name ?? "Claims",
          description: req.body.description ?? "Claims workspace",
          public: req.body.public ?? true, created_by: "u-1",
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/workspaces/ws-1/archive" && req.method === "POST") {
      return {
        status: 200,
        body: { id: "ws-1", tenant_id: "t-42", name: "Claims", description: "Claims workspace",
          public: true, created_by: "u-1", archived_at: "2026-07-12T00:00:00Z",
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/workspaces/ws-1/restore" && req.method === "POST") {
      return {
        status: 200,
        body: { id: "ws-1", tenant_id: "t-42", name: "Claims", description: "Claims workspace",
          public: true, created_by: "u-1", archived_at: null,
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/workspaces/ws-1/content-groups/g-c1" && req.method === "PUT") {
      return { status: 200, body: { workspace_id: "ws-1", group_id: "g-c1", status: "linked" } };
    }
    if (req.path === "/api/v1/workspaces/ws-1/content-groups/g-c1" && req.method === "DELETE") {
      return { status: 200, body: { workspace_id: "ws-1", group_id: "g-c1", status: "unlinked" } };
    }
    // --- rbac: general group create (content groups) ---------------------------
    if (req.path === "/api/v1/groups" && req.method === "POST") {
      return {
        status: 201,
        body: { id: "g-c1", tenant_id: "t-42", name: req.body.name,
          description: req.body.description ?? "", group_type: req.body.group_type,
          system: false, auto_generated: false,
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    // --- rbac: general group update (name/description only) --------------------
    if (req.path === "/api/v1/groups/g-c1" && req.method === "PATCH") {
      return {
        status: 200,
        body: { id: "g-c1", tenant_id: "t-42", name: req.body.name ?? "Underwriting docs",
          description: req.body.description ?? "", group_type: "content",
          system: false, auto_generated: false,
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-13T00:00:00Z" },
      };
    }
    // --- rbac: bulk membership ({results, succeeded, failed} — partial failure)
    if (req.path === "/api/v1/groups/g-1/members:bulk" && req.method === "POST") {
      const ops: { op: string; user_id: string }[] = req.body.operations;
      return {
        status: 200,
        body: {
          results: ops.map((o) => ({
            user_id: o.user_id, op: o.op,
            ok: o.user_id !== "u-missing",
            ...(o.user_id === "u-missing" ? { code: "NOT_FOUND" } : {}),
          })),
          succeeded: ops.filter((o) => o.user_id !== "u-missing").length,
          failed: ops.filter((o) => o.user_id === "u-missing").length,
        },
      };
    }
    // --- rbac: custom-role CRUD (system roles 409 SYSTEM_IMMUTABLE) -----------
    if (req.path === "/api/v1/roles" && req.method === "POST") {
      return {
        status: 201,
        body: { id: "r-new", tenant_id: "t-42", name: req.body.name, system: false,
          version: 1, actions: req.body.actions,
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/roles/r-new" && req.method === "PATCH") {
      return {
        status: 200,
        body: { id: "r-new", tenant_id: "t-42", name: req.body.name, system: false,
          version: 2, actions: ["case.case.read"],
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/roles/r-new/actions" && req.method === "PUT") {
      return {
        status: 200,
        body: { id: "r-new", tenant_id: "t-42", name: "Triage", system: false,
          version: 2, actions: req.body.actions,
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/roles/r-new" && req.method === "DELETE") {
      return { status: 204 };
    }
    if (req.path.startsWith("/api/v1/roles/r-sys") ) {
      // Every mutation on a system role is the same downstream 409.
      return {
        status: 409,
        body: { error: { code: "SYSTEM_IMMUTABLE", message: "system roles cannot be modified", trace_id: "t" } },
      };
    }
    // --- rbac: content grants ---------------------------------------------------
    if (req.path === "/api/v1/grants" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { subject_type: "user", subject_id: "u-1", level: "owner",
              provenance: "implicit_creator", grant_id: "gr-1", workspace_id: "ws-1" },
            { subject_type: "group", subject_id: "g-c1", level: "editor",
              provenance: "direct", grant_id: "gr-2", workspace_id: "ws-1" },
            { subject_type: "user", subject_id: "u-2", level: "editor",
              provenance: "via_group", via: "Underwriters", grant_id: "gr-2", workspace_id: "ws-1" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/grants" && req.method === "POST") {
      return {
        status: 201,
        body: { id: "gr-3", tenant_id: "t-42", workspace_id: req.body.workspace_id,
          resource_urn: req.body.resource_urn, subject_type: req.body.subject.type,
          subject_id: req.body.subject.id, level: req.body.level, implicit: false,
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
      };
    }
    if (req.path === "/api/v1/grants/gr-3" && req.method === "DELETE") {
      return { status: 204 };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = adminLifecycle();
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("Tier 4b: identity user lifecycle", () => {
  it("updateUser PATCHes {full_name} and maps the bare User back", async () => {
    const { body, requests } = await run(
      `mutation { updateUser(id: "u-1", fullName: "Ada Lovelace", idempotencyKey: "idem-u") { id fullName status } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateUser).toMatchObject({ id: "u-1", fullName: "Ada Lovelace", status: "active" });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/users/u-1");
    expect(patch?.body).toEqual({ full_name: "Ada Lovelace" });
    expect(patch?.headers["idempotency-key"]).toBe("idem-u");
  });

  it("deactivateUser surfaces the real last-admin 409 (no override) as CONFLICT", async () => {
    const { body } = await run(`mutation { deactivateUser(id: "u-1") { id status } }`);
    expect(body?.data?.deactivateUser ?? null).toBeNull();
    expect(body?.errors?.[0]?.extensions?.code).toBe("CONFLICT");
    expect(body?.errors?.[0]?.message).toBe("cannot deactivate the last tenant admin");
  });

  it("deactivateUser(overrideLastAdmin: true) passes ?override_last_admin=true through", async () => {
    const { body, requests } = await run(
      `mutation { deactivateUser(id: "u-1", overrideLastAdmin: true) { id status } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deactivateUser).toMatchObject({ id: "u-1", status: "deactivated" });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/users/u-1/deactivate");
    expect(post?.search.get("override_last_admin")).toBe("true");
  });

  it("deactivates a non-admin user without the override flag", async () => {
    const { body, requests } = await run(`mutation { deactivateUser(id: "u-2") { id status } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deactivateUser).toMatchObject({ id: "u-2", status: "deactivated" });
    const post = requests.find((r) => r.path === "/api/v1/users/u-2/deactivate");
    expect(post?.search.get("override_last_admin")).toBeNull();
  });

  it("resendUserInvite POSTs /invite/resend and deleteUser returns true on 204", async () => {
    const resend = await run(`mutation { resendUserInvite(id: "u-2") { id status } }`);
    expect(resend.body?.errors).toBeUndefined();
    expect((resend.body?.data as any).resendUserInvite).toMatchObject({ id: "u-2", status: "invited" });
    expect(resend.requests.some((r) => r.method === "POST" && r.path === "/api/v1/users/u-2/invite/resend")).toBe(true);

    const del = await run(`mutation { deleteUser(id: "u-2") }`);
    expect(del.body?.errors).toBeUndefined();
    expect((del.body?.data as any).deleteUser).toBe(true);
    expect(del.requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/users/u-2")).toBe(true);
  });
});

describe("Tier 4b: identity service-account lifecycle (api_key shown once)", () => {
  it("createServiceAccount sends the snake body and returns the apiKey VERBATIM", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateServiceAccountInput!, $k: String) {
        createServiceAccount(input: $input, idempotencyKey: $k) {
          serviceAccount { id name scopes expiresAt urn }
          apiKey
        }
      }`,
      { input: { name: "ci-bot", scopes: ["pipeline.run.create"], expiresAt: "2027-01-01T00:00:00Z" }, k: "idem-sa" },
    );
    expect(body?.errors).toBeUndefined();
    const created: any = (body?.data as any).createServiceAccount;
    // The one-time key passes through byte-for-byte (wr_sa_<id>.<secret>).
    expect(created.apiKey).toBe("wr_sa_sa-new.s3cr3t-once");
    expect(created.serviceAccount).toMatchObject({
      id: "sa-new", name: "ci-bot", scopes: ["pipeline.run.create"],
      expiresAt: "2027-01-01T00:00:00Z", urn: "wr:t-42:identity:service_account/sa-new",
    });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/service-accounts");
    expect(post?.body).toEqual({ name: "ci-bot", scopes: ["pipeline.run.create"], expires_at: "2027-01-01T00:00:00Z" });
    expect(post?.headers["idempotency-key"]).toBe("idem-sa");
  });

  it("rotateServiceAccount returns the NEW one-time apiKey", async () => {
    const { body, requests } = await run(
      `mutation { rotateServiceAccount(id: "sa-1") { serviceAccount { id name } apiKey } }`,
    );
    expect(body?.errors).toBeUndefined();
    const rotated: any = (body?.data as any).rotateServiceAccount;
    expect(rotated.apiKey).toBe("wr_sa_sa-1.r0tated-once");
    expect(rotated.serviceAccount).toMatchObject({ id: "sa-1", name: "etl-bot" });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/service-accounts/sa-1/rotate")).toBe(true);
  });

  it("revokeServiceAccount returns true on 204", async () => {
    const { body, requests } = await run(`mutation { revokeServiceAccount(id: "sa-1") }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).revokeServiceAccount).toBe(true);
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/service-accounts/sa-1")).toBe(true);
  });
});

describe("Tier 4b: rbac workspace lifecycle + content groups", () => {
  it("archive then restore derives the archived flag from archived_at", async () => {
    const arch = await run(`mutation { archiveWorkspace(id: "ws-1") { id archived archivedAt } }`);
    expect(arch.body?.errors).toBeUndefined();
    expect((arch.body?.data as any).archiveWorkspace).toMatchObject({
      id: "ws-1", archived: true, archivedAt: "2026-07-12T00:00:00Z",
    });

    const rest = await run(`mutation { restoreWorkspace(id: "ws-1") { id archived archivedAt } }`);
    expect(rest.body?.errors).toBeUndefined();
    expect((rest.body?.data as any).restoreWorkspace).toMatchObject({
      id: "ws-1", archived: false, archivedAt: null,
    });
  });

  it("updateWorkspace PATCHes only the provided fields", async () => {
    const { body, requests } = await run(
      `mutation($input: UpdateWorkspaceInput!) { updateWorkspace(id: "ws-1", input: $input) { id name public } }`,
      { input: { name: "Claims 2", public: false } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateWorkspace).toMatchObject({ id: "ws-1", name: "Claims 2", public: false });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/workspaces/ws-1");
    expect(patch?.body).toEqual({ name: "Claims 2", public: false }); // description absent → unchanged
  });

  it("link/unlink content group hit PUT/DELETE /workspaces/{id}/content-groups/{gid}", async () => {
    const link = await run(`mutation { linkWorkspaceContentGroup(workspaceId: "ws-1", groupId: "g-c1") }`);
    expect(link.body?.errors).toBeUndefined();
    expect((link.body?.data as any).linkWorkspaceContentGroup).toBe(true);
    expect(link.requests.some((r) => r.method === "PUT" && r.path === "/api/v1/workspaces/ws-1/content-groups/g-c1")).toBe(true);

    const unlink = await run(`mutation { unlinkWorkspaceContentGroup(workspaceId: "ws-1", groupId: "g-c1") }`);
    expect(unlink.body?.errors).toBeUndefined();
    expect((unlink.body?.data as any).unlinkWorkspaceContentGroup).toBe(true);
    expect(unlink.requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/workspaces/ws-1/content-groups/g-c1")).toBe(true);
  });

  it("createGroup(groupType: CONTENT) lowercases to group_type=content on the wire", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateGroupInput!) { createGroup(input: $input) { id name groupType } }`,
      { input: { name: "Underwriting docs", description: "content boundary", groupType: "CONTENT" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createGroup).toMatchObject({ id: "g-c1", name: "Underwriting docs", groupType: "content" });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/groups");
    expect(post?.body).toMatchObject({ name: "Underwriting docs", description: "content boundary", group_type: "content" });
  });

  it("updateGroup PATCHes only the provided fields (name/description) and maps the Group back", async () => {
    const { body, requests } = await run(
      `mutation($input: UpdateGroupInput!) { updateGroup(input: $input, idempotencyKey: "idem-g") { id name groupType } }`,
      { input: { id: "g-c1", name: "Underwriting docs (v2)" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateGroup).toMatchObject({ id: "g-c1", name: "Underwriting docs (v2)", groupType: "content" });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/groups/g-c1");
    // description was omitted → not sent (partial update leaves it untouched).
    expect(patch?.body).toEqual({ name: "Underwriting docs (v2)" });
    expect(patch?.headers["idempotency-key"]).toBe("idem-g");
  });
});

describe("Tier 4b: rbac bulk group membership", () => {
  it("maps the real per-entry partial-failure report ({results, succeeded, failed})", async () => {
    const { body, requests } = await run(
      `mutation($ops: [GroupMemberOpInput!]!) {
        bulkGroupMembership(groupId: "g-1", operations: $ops, idempotencyKey: "idem-b") {
          succeeded failed results { userId op ok code }
        }
      }`,
      { ops: [
        { op: "ADD", userId: "u-3" },
        { op: "ADD", userId: "u-missing" },
        { op: "REMOVE", userId: "u-4" },
      ] },
    );
    expect(body?.errors).toBeUndefined();
    const res: any = (body?.data as any).bulkGroupMembership;
    expect(res).toMatchObject({ succeeded: 2, failed: 1 });
    expect(res.results).toEqual([
      { userId: "u-3", op: "add", ok: true, code: null },
      { userId: "u-missing", op: "add", ok: false, code: "NOT_FOUND" },
      { userId: "u-4", op: "remove", ok: true, code: null },
    ]);
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/groups/g-1/members:bulk");
    // The GraphQL op enum lowercases to store.BulkMemberOp's wire values.
    expect(post?.body).toEqual({ operations: [
      { op: "add", user_id: "u-3" },
      { op: "add", user_id: "u-missing" },
      { op: "remove", user_id: "u-4" },
    ] });
    expect(post?.headers["idempotency-key"]).toBe("idem-b");
  });
});

describe("Tier 4b: rbac custom-role CRUD", () => {
  it("createRole POSTs {name, actions} and maps the created role", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateRoleInput!) { createRole(input: $input) { id name system version actions } }`,
      { input: { name: "Triage", actions: ["case.case.read", "case.case.update"] } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createRole).toMatchObject({
      id: "r-new", name: "Triage", system: false, version: 1,
      actions: ["case.case.read", "case.case.update"],
    });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/roles");
    expect(post?.body).toEqual({ name: "Triage", actions: ["case.case.read", "case.case.update"] });
  });

  it("setRoleActions PUTs the replacement action set", async () => {
    const { body, requests } = await run(
      `mutation { setRoleActions(id: "r-new", actions: ["case.case.read"]) { id actions } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).setRoleActions).toMatchObject({ id: "r-new", actions: ["case.case.read"] });
    const put = requests.find((r) => r.method === "PUT" && r.path === "/api/v1/roles/r-new/actions");
    expect(put?.body).toEqual({ actions: ["case.case.read"] });
  });

  it("renameRole PATCHes rename-only and deleteRole returns true on 204", async () => {
    const rename = await run(`mutation { renameRole(id: "r-new", name: "Triage L2") { id name } }`);
    expect(rename.body?.errors).toBeUndefined();
    expect((rename.body?.data as any).renameRole).toMatchObject({ id: "r-new", name: "Triage L2" });
    const patch = rename.requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/roles/r-new");
    expect(patch?.body).toEqual({ name: "Triage L2" });

    const del = await run(`mutation { deleteRole(id: "r-new") }`);
    expect(del.body?.errors).toBeUndefined();
    expect((del.body?.data as any).deleteRole).toBe(true);
  });

  it("surfaces the system-role 409 SYSTEM_IMMUTABLE verbatim (CONFLICT, real message)", async () => {
    for (const mutation of [
      `mutation { renameRole(id: "r-sys", name: "x") { id } }`,
      `mutation { setRoleActions(id: "r-sys", actions: ["a.b.c"]) { id } }`,
      `mutation { deleteRole(id: "r-sys") }`,
    ]) {
      const { body } = await run(mutation);
      const err = body?.errors?.[0];
      expect(err?.extensions?.code).toBe("CONFLICT");
      expect(err?.message).toBe("system roles cannot be modified");
      expect(err?.extensions?.httpStatus).toBe(409);
    }
  });
});

describe("Tier 4b: rbac content grants", () => {
  it("contentGrants threads resource_urn and maps provenance/via rows", async () => {
    const { body, requests } = await run(
      `{ contentGrants(resourceUrn: "wr:t-42:dataset:dataset/d1") {
          subjectType subjectId level provenance via grantId workspaceId
        } }`,
    );
    expect(body?.errors).toBeUndefined();
    const rows: any[] = (body?.data as any).contentGrants;
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({ subjectType: "user", subjectId: "u-1", level: "owner",
      provenance: "implicit_creator", via: null, grantId: "gr-1", workspaceId: "ws-1" });
    expect(rows[2]).toMatchObject({ subjectType: "user", subjectId: "u-2",
      provenance: "via_group", via: "Underwriters", grantId: "gr-2" });
    expect(requests[0]?.search.get("resource_urn")).toBe("wr:t-42:dataset:dataset/d1");
  });

  it("createContentGrant nests the subject object on the wire and maps the grant", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateContentGrantInput!) {
        createContentGrant(input: $input, idempotencyKey: "idem-g") {
          id workspaceId resourceUrn subjectType subjectId level implicit
        }
      }`,
      { input: { workspaceId: "ws-1", resourceUrn: "wr:t-42:dataset:dataset/d1",
        subjectType: "group", subjectId: "g-c1", level: "editor" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createContentGrant).toMatchObject({
      id: "gr-3", workspaceId: "ws-1", resourceUrn: "wr:t-42:dataset:dataset/d1",
      subjectType: "group", subjectId: "g-c1", level: "editor", implicit: false,
    });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/grants");
    // rbac's createGrantRequest takes the NESTED subject shape — not flat fields.
    expect(post?.body).toEqual({
      workspace_id: "ws-1", resource_urn: "wr:t-42:dataset:dataset/d1",
      subject: { type: "group", id: "g-c1" }, level: "editor",
    });
    expect(post?.headers["idempotency-key"]).toBe("idem-g");
  });

  it("deleteContentGrant returns true on 204", async () => {
    const { body, requests } = await run(`mutation { deleteContentGrant(id: "gr-3") }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteContentGrant).toBe(true);
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/grants/gr-3")).toBe(true);
  });
});

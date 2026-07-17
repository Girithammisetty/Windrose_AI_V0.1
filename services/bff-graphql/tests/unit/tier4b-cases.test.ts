/**
 * Tier 4b: case-service ops surfaces.
 *  - lifecycle transitions (assign/unassign/start/resolve/reopen/close/escalate)
 *    returning the full caseView; illegal from-states surface the service's
 *    real 409 INVALID_TRANSITION verbatim.
 *  - comments (create-only read path — there is NO list-comments route) +
 *    timeline pagination.
 *  - async CSV export: 202 → immediate operation re-read (real status, never
 *    fabricated).
 *  - disposition catalog + custom case-fields (purpose int16 → string) + SLA
 *    policy write-only echo.
 * Mocking is at the fetch boundary; real master envelopes on both sides.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();
const single = (res: any) => (res.body.kind === "single" ? res.body.singleResult : null);

/** The full caseView shape case-service returns from GET/PATCH/lifecycle. */
function caseView(overrides: Record<string, unknown> = {}) {
  return {
    id: "c-1", workspace_id: "ws-9", case_number: 7, status: "in_progress", severity: "high",
    assigned_to_id: "u-2", assigned_to_at: "2026-07-10T09:00:00Z", created_by_id: "u-1",
    dataset_urn: "wr:t-42:dataset:dataset/ds-1", dataset_version: "3", row_pk: "row-9",
    due_date: "2026-07-20T00:00:00Z", description: "suspicious claim", custom_fields: {},
    disposition_id: null, resolution_note: "", resolved_at: null, closed_at: null,
    reassign_count: 1, case_version: 4,
    created_at: "2026-07-09T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
    ...overrides,
  };
}

function caseService() {
  return mockFetch((req: CapturedRequest) => {
    // ---- lifecycle transitions ----------------------------------------------
    if (req.path === "/api/v1/cases/c-1/assign" && req.method === "POST") {
      return { status: 200, body: { data: caseView({ status: "draft", assigned_to_id: req.body.assignee_id }) } };
    }
    if (req.path === "/api/v1/cases/c-1/start" && req.method === "POST") {
      return { status: 200, body: { data: caseView({ status: "in_progress" }) } };
    }
    if (req.path === "/api/v1/cases/c-1/resolve" && req.method === "POST") {
      return { status: 200, body: { data: caseView({
        status: "resolved", disposition_id: req.body.disposition_id,
        resolution_note: req.body.resolution_note ?? "", resolved_at: "2026-07-12T10:00:00Z",
      }) } };
    }
    if (req.path === "/api/v1/cases/c-closed/reopen" && req.method === "POST") {
      return { status: 409, body: { error: { code: "INVALID_TRANSITION",
        message: "cannot reopen from closed", trace_id: "tr-409" } } };
    }
    // ---- comments -------------------------------------------------------------
    if (req.path === "/api/v1/cases/c-1/comments" && req.method === "POST") {
      return { status: 201, body: { data: {
        id: "cm-1", case_id: "c-1", author_id: "u-1", body: req.body.body,
        created_at: "2026-07-12T11:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/comments/cm-1" && req.method === "PATCH") {
      // The route echoes ONLY {id, body} — the BFF must not invent the rest.
      return { status: 200, body: { data: { id: "cm-1", body: req.body.body } } };
    }
    if (req.path === "/api/v1/comments/cm-1" && req.method === "DELETE") return { status: 204 };
    // ---- timeline -------------------------------------------------------------
    if (req.path === "/api/v1/cases/c-1/timeline" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "a-2", case_id: "c-1", event_type: "comment.added", actor_type: "user", actor_id: "u-1",
          new_value: { comment_id: "cm-1" }, occurred_at: "2026-07-12T11:00:00.123456789Z" },
        { id: "a-1", case_id: "c-1", event_type: "case.status_changed", actor_type: "agent", actor_id: "agent-triage",
          via_agent: { agent_id: "agent-triage", version: "2" }, old_value: "draft", new_value: "in_progress",
          occurred_at: "2026-07-12T10:00:00.000000001Z" },
      ], page: { next_cursor: "2026-07-12T10:00:00.000000001Z", has_more: true } } };
    }
    // ---- export + operations ---------------------------------------------------
    if (req.path === "/api/v1/cases/export" && req.method === "POST") {
      return { status: 202, body: { data: { operation_id: "op-1" } } };
    }
    if (req.path === "/api/v1/operations/op-1" && req.method === "GET") {
      return { status: 200, body: { data: {
        id: "op-1", kind: "export", status: "succeeded", succeeded: 42, failed: 0, total: 42,
        result: { row_count: 42, object_ref: "t/op-1.csv.gz",
          download_url: "/api/v1/operations/op-1/download", expires_at: "2026-07-12T12:15:00Z" },
      } } };
    }
    // ---- dispositions ------------------------------------------------------------
    if (req.path === "/api/v1/dispositions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "d-1", workspace_id: "ws-9", code: "fraud_confirmed", label: "Fraud confirmed",
          category: "true_positive", requires_note: true, active: true,
          created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/dispositions" && req.method === "POST") {
      if (req.body.code === "dup") {
        return { status: 409, body: { error: { code: "CONFLICT",
          message: "disposition code already exists", trace_id: "tr-dup" } } };
      }
      return { status: 201, body: { data: {
        id: "d-new", workspace_id: "ws-9", code: req.body.code, label: req.body.label,
        category: req.body.category, requires_note: req.body.requires_note ?? false,
        active: req.body.active ?? true,
        created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/dispositions/d-1" && req.method === "PATCH") {
      return { status: 200, body: { data: {
        id: "d-1", workspace_id: "ws-9", code: "fraud_confirmed",
        label: req.body.label ?? "Fraud confirmed", category: req.body.category ?? "true_positive",
        requires_note: req.body.requires_note, active: req.body.active ?? true,
        created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
      } } };
    }
    // ---- case-fields --------------------------------------------------------------
    if (req.path === "/api/v1/case-fields" && req.method === "GET") {
      return { status: 200, body: { data: [
        // purpose is the int16 wire form: 0=create, 1=update, 2=both.
        { id: "f-1", workspace_id: "ws-9", name: "adjuster_notes", data_type: "text", purpose: 2,
          field_meta: {}, created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-01T00:00:00Z" },
        { id: "f-2", workspace_id: "ws-9", query_urn: "wr:t-42:query:query/q-1", name: "score",
          data_type: "float", purpose: 0, field_meta: {}, created_at: "2026-07-02T00:00:00Z",
          updated_at: "2026-07-02T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/case-fields" && req.method === "POST") {
      return { status: 201, body: { data: {
        id: "f-new", workspace_id: "ws-9", query_urn: req.body.query_urn ?? "", name: req.body.name,
        data_type: req.body.data_type, purpose: req.body.purpose === "update" ? 1 : req.body.purpose === "create" ? 0 : 2,
        field_meta: req.body.field_meta ?? {},
        created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/case-fields/f-1" && req.method === "PATCH") {
      return { status: 200, body: { data: {
        id: "f-1", workspace_id: "ws-9", name: "adjuster_notes", data_type: "text",
        purpose: req.body.purpose === "update" ? 1 : req.body.purpose === "create" ? 0 : 2,
        field_meta: req.body.field_meta ?? {},
        created_at: "2026-07-01T00:00:00Z", updated_at: "2026-07-13T00:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/case-fields/f-1" && req.method === "DELETE") {
      if (req.search.get("orphan") !== "true") {
        return { status: 409, body: { error: { code: "FIELD_IN_USE",
          message: "field has values on open cases", trace_id: "tr-fiu" } } };
      }
      return { status: 204 };
    }
    // ---- SLA policy ------------------------------------------------------------------
    if (req.path === "/api/v1/sla-policy" && req.method === "PUT") {
      return { status: 200, body: { data: {
        workspace_id: "ws-9",
        warn_before_seconds: req.body.warn_before_seconds > 0 ? req.body.warn_before_seconds : 86400,
        on_breach: req.body.on_breach || "auto_unassign",
        max_reassign_count: req.body.max_reassign_count > 0 ? req.body.max_reassign_count : 3,
      } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("case lifecycle transitions (case-service passthrough)", () => {
  it("assignCase POSTs assignee_id + Idempotency-Key and returns the full caseView", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          assignCase(id: "c-1", assigneeId: "u-9", idempotencyKey: "ik-1") {
            id status caseVersion reassignCount urn
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).assignCase).toMatchObject({
      id: "c-1", status: "DRAFT", caseVersion: 4, reassignCount: 1, urn: "wr:t-42:case:case/c-1",
    });
    const post = requests.find((r) => r.path === "/api/v1/cases/c-1/assign");
    expect(post?.body).toEqual({ assignee_id: "u-9" });
    expect(post?.headers["idempotency-key"]).toBe("ik-1");
  });

  it("startCase transitions draft → in_progress via the real route", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { startCase(id: "c-1") { id status } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).startCase.status).toBe("IN_PROGRESS");
    expect(requests.some((r) => r.path === "/api/v1/cases/c-1/start" && r.method === "POST")).toBe(true);
  });

  it("resolveCase threads disposition_id + resolution_note and maps the resolution fields", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          resolveCase(id: "c-1", dispositionId: "d-1", resolutionNote: "confirmed staged accident") {
            id status dispositionId resolutionNote resolvedAt
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).resolveCase).toMatchObject({
      status: "RESOLVED", dispositionId: "d-1",
      resolutionNote: "confirmed staged accident", resolvedAt: "2026-07-12T10:00:00Z",
    });
    const post = requests.find((r) => r.path === "/api/v1/cases/c-1/resolve");
    expect(post?.body).toEqual({ disposition_id: "d-1", resolution_note: "confirmed staged accident" });
  });

  it("an illegal transition surfaces the service's 409 INVALID_TRANSITION verbatim as CONFLICT", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { reopenCase(id: "c-closed") { id } }` },
      { contextValue: ctx },
    );
    const err = single(res)?.errors?.[0];
    expect(err?.extensions?.code).toBe("CONFLICT");
    expect(err?.message).toBe("cannot reopen from closed");
  });
});

describe("case comments + timeline", () => {
  it("addCaseComment 201 maps the full comment (the only read of the body there is)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          addCaseComment(caseId: "c-1", body: "flagging for SIU review", idempotencyKey: "ik-c") {
            id caseId authorId body createdAt
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).addCaseComment).toEqual({
      id: "cm-1", caseId: "c-1", authorId: "u-1",
      body: "flagging for SIU review", createdAt: "2026-07-12T11:00:00Z",
    });
    const post = requests.find((r) => r.path === "/api/v1/cases/c-1/comments");
    expect(post?.body).toEqual({ body: "flagging for SIU review" });
    expect(post?.headers["idempotency-key"]).toBe("ik-c");
  });

  it("updateCaseComment returns only what the thin {id, body} echo carries — the rest is null", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { updateCaseComment(id: "cm-1", body: "edited") { id body caseId authorId createdAt } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateCaseComment).toEqual({
      id: "cm-1", body: "edited", caseId: null, authorId: null, createdAt: null,
    });
  });

  it("deleteCaseComment resolves true on the 204", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { deleteCaseComment(id: "cm-1") }` },
      { contextValue: ctx },
    );
    expect(single(res)?.data).toEqual({ deleteCaseComment: true });
  });

  it("caseTimeline maps the activity page + RFC3339Nano cursor pagination", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query {
          caseTimeline(caseId: "c-1", first: 2) {
            nodes { id eventType actorType actorId viaAgent oldValue newValue occurredAt }
            pageInfo { nextCursor hasMore }
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).caseTimeline;
    expect(conn.nodes).toHaveLength(2);
    expect(conn.nodes[0]).toMatchObject({
      id: "a-2", eventType: "comment.added", actorType: "user", actorId: "u-1",
      newValue: { comment_id: "cm-1" },
    });
    expect(conn.nodes[1]).toMatchObject({
      id: "a-1", eventType: "case.status_changed", actorType: "agent",
      viaAgent: { agent_id: "agent-triage", version: "2" },
      oldValue: "draft", newValue: "in_progress",
    });
    expect(conn.pageInfo).toEqual({ nextCursor: "2026-07-12T10:00:00.000000001Z", hasMore: true });
    const get = requests.find((r) => r.path === "/api/v1/cases/c-1/timeline");
    expect(get?.search.get("limit")).toBe("2");
  });
});

describe("case export (async operation)", () => {
  it("exportCases POSTs the filter, then returns the REAL polled operation — never a fabricated status", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($filter: JSON) {
          exportCases(filter: $filter, format: "csv") {
            id kind status succeeded total rowCount downloadUrl expiresAt error
          }
        }`,
        variables: { filter: { status: "resolved" } } },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).exportCases).toEqual({
      id: "op-1", kind: "export", status: "succeeded", succeeded: 42, total: 42,
      rowCount: 42, downloadUrl: "/api/v1/operations/op-1/download",
      expiresAt: "2026-07-12T12:15:00Z", error: null,
    });
    const post = requests.find((r) => r.path === "/api/v1/cases/export");
    expect(post?.body).toEqual({ filter: { status: "resolved" }, format: "csv" });
    // The 202 was followed by the real operation read.
    expect(requests.some((r) => r.path === "/api/v1/operations/op-1" && r.method === "GET")).toBe(true);
  });
});

describe("disposition catalog", () => {
  it("dispositions lists the workspace catalog", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query { dispositions { id code label category requiresNote active } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).dispositions).toEqual([
      { id: "d-1", code: "fraud_confirmed", label: "Fraud confirmed",
        category: "true_positive", requiresNote: true, active: true },
    ]);
  });

  it("createDisposition maps camel input to the snake body; a duplicate code 409s as CONFLICT", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          createDisposition(input: { code: "benign_dup", label: "Benign duplicate",
            category: "benign", requiresNote: false }) { id code category requiresNote active }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createDisposition).toMatchObject({
      id: "d-new", code: "benign_dup", category: "benign", requiresNote: false, active: true,
    });
    const post = requests.find((r) => r.path === "/api/v1/dispositions" && r.method === "POST");
    expect(post?.body).toMatchObject({ code: "benign_dup", label: "Benign duplicate",
      category: "benign", requires_note: false });

    const dup = await server.executeOperation(
      { query: `mutation { createDisposition(input: { code: "dup", label: "x", category: "other" }) { id } }` },
      { contextValue: ctx },
    );
    expect(single(dup)?.errors?.[0]?.extensions?.code).toBe("CONFLICT");
  });

  it("updateDisposition PATCHes and returns the updated entry", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          updateDisposition(id: "d-1", input: { label: "Fraud (confirmed)", requiresNote: true, active: false }) {
            id label requiresNote active
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateDisposition).toMatchObject({
      id: "d-1", label: "Fraud (confirmed)", requiresNote: true, active: false,
    });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/dispositions/d-1");
    expect(patch?.body).toMatchObject({ label: "Fraud (confirmed)", requires_note: true, active: false });
  });
});

describe("custom case-fields", () => {
  it("caseFields maps the int16 purpose back to its string form", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query { caseFields { id name dataType purpose queryUrn } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).caseFields).toEqual([
      { id: "f-1", name: "adjuster_notes", dataType: "text", purpose: "both", queryUrn: null },
      { id: "f-2", name: "score", dataType: "float", purpose: "create", queryUrn: "wr:t-42:query:query/q-1" },
    ]);
  });

  it("createCaseField sends the STRING purpose and maps the int16 echo back", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          createCaseField(input: { name: "siu_referral", dataType: "boolean", purpose: "update" }) {
            id name dataType purpose
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createCaseField).toMatchObject({
      id: "f-new", name: "siu_referral", dataType: "boolean", purpose: "update",
    });
    const post = requests.find((r) => r.path === "/api/v1/case-fields" && r.method === "POST");
    expect(post?.body).toMatchObject({ name: "siu_referral", data_type: "boolean", purpose: "update" });
  });

  it("updateCaseField PATCHes only purpose/fieldMeta and maps the int16 echo back", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          updateCaseField(input: { id: "f-1", purpose: "create", fieldMeta: { label: "Adjuster notes" } }) {
            id name dataType purpose fieldMeta
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateCaseField).toMatchObject({
      id: "f-1", name: "adjuster_notes", dataType: "text", purpose: "create",
      fieldMeta: { label: "Adjuster notes" },
    });
    const patch = requests.find((r) => r.path === "/api/v1/case-fields/f-1" && r.method === "PATCH");
    // name/dataType are immutable so never sent; the wire body carries the STRING
    // purpose + snake_case field_meta only.
    expect(patch?.body).toEqual({ purpose: "create", field_meta: { label: "Adjuster notes" } });
  });

  it("deleteCaseField surfaces FIELD_IN_USE as CONFLICT and succeeds with orphan: true", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const blocked = await server.executeOperation(
      { query: `mutation { deleteCaseField(id: "f-1") }` },
      { contextValue: ctx },
    );
    expect(single(blocked)?.errors?.[0]?.extensions?.code).toBe("CONFLICT");

    const orphaned = await server.executeOperation(
      { query: `mutation { deleteCaseField(id: "f-1", orphan: true) }` },
      { contextValue: ctx },
    );
    expect(single(orphaned)?.data).toEqual({ deleteCaseField: true });
    const del = requests.filter((r) => r.method === "DELETE").at(-1);
    expect(del?.search.get("orphan")).toBe("true");
  });
});

describe("SLA policy (write-only)", () => {
  it("putCaseSlaPolicy PUTs the snake body and maps the effective-policy echo", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = caseService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          putCaseSlaPolicy(input: { warnBeforeSeconds: 3600, onBreach: "escalate",
            escalateTo: "u-lead", maxReassignCount: 5 }) {
            workspaceId warnBeforeSeconds onBreach maxReassignCount
          }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).putCaseSlaPolicy).toEqual({
      workspaceId: "ws-9", warnBeforeSeconds: 3600, onBreach: "escalate", maxReassignCount: 5,
    });
    const put = requests.find((r) => r.method === "PUT");
    expect(put?.body).toEqual({
      warn_before_seconds: 3600, on_breach: "escalate", escalate_to: "u-lead", max_reassign_count: 5,
    });
  });
});

import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { ErrorCode } from "../../src/errors/errors.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** A downstream double serving OpenAPI-shaped responses for a claims case. */
function downstream() {
  return mockFetch((req: CapturedRequest) => {
    // case-service
    if (req.path === "/api/v1/cases/case-1") {
      return {
        status: 200,
        body: {
          id: "case-1",
          case_number: 7,
          status: "in_progress",
          severity: "high",
          assigned_to_id: "user-1",
          dataset_urn: "wr:t-42:dataset:dataset/ds-9",
        },
      };
    }
    if (req.path === "/api/v1/cases/forbidden") {
      return { status: 403, body: { error: { code: "PERMISSION_DENIED", message: "no access", trace_id: "tr-403" } } };
    }
    // identity-service batch
    if (req.path === "/api/v1/users") {
      return { status: 200, body: { data: [{ id: "user-1", email: "a@x.com", full_name: "Ann" }], page: { has_more: false } } };
    }
    // dataset-service batch
    if (req.path === "/api/v1/datasets") {
      return { status: 200, body: { data: [{ id: "ds-9", name: "claims-2026" }], page: { has_more: false } } };
    }
    // agent-runtime proposals-by-resource
    if (req.path === "/api/v1/proposals") {
      return { status: 200, body: { data: [], page: { has_more: false } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("federated case composition (AC-2) + JWT passthrough (AC-3)", () => {
  it("composes case + assignee (identity) + sourceDataset (dataset) in one query", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      {
        query: `{ case(id:"case-1") {
          id urn status severity
          assignee { email fullName }
          sourceDataset { name }
          proposals { id }
        } }`,
      },
      { contextValue: ctx },
    );

    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const c: any = body?.data?.case;
    expect(c.status).toBe("IN_PROGRESS");
    expect(c.severity).toBe("HIGH");
    expect(c.urn).toBe("wr:t-42:case:case/case-1");
    expect(c.assignee.email).toBe("a@x.com");
    expect(c.sourceDataset.name).toBe("claims-2026");
    expect(c.proposals).toEqual([]);

    // Every downstream call carried the caller's JWT verbatim (passthrough).
    for (const r of requests) {
      expect(r.headers["authorization"]).toMatch(/^Bearer /);
    }
    // Nested hydration went through the batch endpoints (loaders), one each.
    expect(requests.filter((r) => r.path === "/api/v1/users")).toHaveLength(1);
    expect(requests.filter((r) => r.path === "/api/v1/datasets")).toHaveLength(1);
  });

  it("surfaces the downstream risk tier verbatim on Proposal.riskTier (passthrough, no decision)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req: CapturedRequest) => {
      if (req.path === "/api/v1/proposals") {
        return {
          status: 200,
          body: {
            data: [
              { id: "pr-1", tool: "assign_case", tier: "write-proposal", status: "pending" },
              { id: "pr-2", tool: "applyDisposition", tier: "write-direct", status: "pending" },
              { id: "pr-3", tool: "purge_case", side_effects: "destructive", status: "pending" },
              { id: "pr-4", tool: "mystery", status: "pending" }, // no tier at all
            ],
            page: { has_more: false },
          },
        };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      { query: `{ proposalsInbox(status: PENDING) { nodes { id tool riskTier } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const nodes: any[] = (body?.data as any)?.proposalsInbox?.nodes ?? [];
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n.riskTier]));
    expect(byId["pr-1"]).toBe("write-proposal"); // verbatim
    expect(byId["pr-2"]).toBe("write-direct"); // verbatim
    expect(byId["pr-3"]).toBe("write-direct"); // derived from destructive side-effect
    expect(byId["pr-4"]).toBe("unknown"); // missing classification → safe sentinel
  });

  it("maps a downstream 403 to PERMISSION_DENIED with the trace id (BFF-FR-051)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      { query: `{ case(id:"forbidden") { id } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.data?.case).toBeNull();
    expect(body?.errors?.[0]?.extensions?.code).toBe(ErrorCode.PERMISSION_DENIED);
    expect(body?.errors?.[0]?.extensions?.service).toBe("case-service");
    expect(body?.errors?.[0]?.extensions?.traceId).toBe("tr-403");
  });
});

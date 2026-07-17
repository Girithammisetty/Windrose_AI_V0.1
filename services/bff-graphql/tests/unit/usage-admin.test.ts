/**
 * Budget + rate-card admin CRUD (usage-service). Every response shape mirrors
 * the real downstream route bodies (budgetView / rateCardView / Page envelope),
 * read from the Go handler structs — see services/usage-service/internal/api.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function usage() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/budgets" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "b-1", scope: { workspace_id: "ws-1" }, meter_key: "api_calls", window: "calendar_month",
              limit_value: 100, thresholds: [80, 95, 100], action_at_100: "alert_only", status: "active",
              created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/budgets" && req.method === "POST") {
      return {
        status: 201,
        body: {
          data: {
            id: "b-new", scope: { workspace_id: req.body.scope?.workspace_id },
            meter_key: req.body.meter_key, window: req.body.window, limit_value: req.body.limit_value,
            thresholds: [80, 95, 100], action_at_100: req.body.action_at_100 ?? "alert_only", status: "active",
            created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/budgets/b-1" && req.method === "GET") {
      return {
        status: 200,
        body: { data: { id: "b-1", scope: { workspace_id: "ws-1" }, meter_key: "api_calls", window: "calendar_month",
          limit_value: 100, thresholds: [80, 95, 100], action_at_100: "alert_only", status: "active",
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" } },
      };
    }
    if (req.path === "/api/v1/budgets/b-1" && req.method === "PATCH") {
      return {
        status: 200,
        body: { data: { id: "b-1", scope: { workspace_id: "ws-1" }, meter_key: "api_calls", window: "calendar_month",
          limit_value: req.body.limit_value ?? 100, thresholds: [80, 95, 100],
          action_at_100: req.body.action_at_100 ?? "alert_only", status: "active",
          created_at: "2026-01-01T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" } },
      };
    }
    if (req.path === "/api/v1/budgets/b-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    if (req.path === "/api/v1/rate-cards" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "rc-1", version: 1, effective_from: "2026-01-01", status: "active",
              items: { api_calls: 0.001 }, created_at: "2026-01-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/rate-cards" && req.method === "POST") {
      return {
        status: 201,
        body: { data: { id: "rc-new", version: req.body.version, effective_from: req.body.effective_from,
          status: "draft", items: req.body.items, created_at: "2026-07-11T00:00:00Z" } },
      };
    }
    if (req.path === "/api/v1/rate-cards/rc-new/activate" && req.method === "POST") {
      return {
        status: 200,
        body: { data: { id: "rc-new", version: 2, effective_from: "2026-08-01", status: "active",
          items: { api_calls: 0.002 }, created_at: "2026-07-11T00:00:00Z" } },
      };
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

describe("admin: usage budgets", () => {
  it("lists budgets (limit_value -> limitUsd, action_at_100 -> actionAt100)", async () => {
    const { body } = await run(
      `{ budgets { nodes { id scope meterKey window limitUsd thresholds actionAt100 status } } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).budgets.nodes[0]).toMatchObject({
      id: "b-1", scope: "workspace/ws-1", meterKey: "api_calls", window: "calendar_month",
      limitUsd: 100, thresholds: [80, 95, 100], actionAt100: "alert_only", status: "active",
    });
  });

  it("reads a single budget by id", async () => {
    const { body } = await run(`{ budget(id: "b-1") { id limitUsd } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).budget).toMatchObject({ id: "b-1", limitUsd: 100 });
  });

  it("createBudget POSTs a nested scope + snake_case body and maps the result", async () => {
    const { body, requests } = await run(
      `mutation($input: CreateBudgetInput!, $k: String) { createBudget(input: $input, idempotencyKey: $k) { id scope limitUsd actionAt100 } }`,
      { input: { workspaceId: "ws-9", meterKey: "tokens", window: "calendar_month", limitUsd: 250, actionAt100: "hard_stop" }, k: "idem-b" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createBudget).toMatchObject({ id: "b-new", scope: "workspace/ws-9", limitUsd: 250, actionAt100: "hard_stop" });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/budgets");
    expect(post?.body).toMatchObject({
      scope: { workspace_id: "ws-9" }, meter_key: "tokens", window: "calendar_month",
      limit_value: 250, action_at_100: "hard_stop",
    });
    expect(post?.headers["idempotency-key"]).toBe("idem-b");
  });

  it("updateBudget PATCHes the limit and deleteBudget returns true on 204", async () => {
    const upd = await run(
      `mutation($input: UpdateBudgetInput!) { updateBudget(id: "b-1", input: $input) { id limitUsd } }`,
      { input: { limitUsd: 500 } },
    );
    expect(upd.body?.errors).toBeUndefined();
    expect((upd.body?.data as any).updateBudget).toMatchObject({ id: "b-1", limitUsd: 500 });
    const patch = upd.requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/budgets/b-1");
    expect(patch?.body).toMatchObject({ limit_value: 500 });

    const del = await run(`mutation { deleteBudget(id: "b-1") }`);
    expect(del.body?.errors).toBeUndefined();
    expect((del.body?.data as any).deleteBudget).toBe(true);
  });
});

describe("admin: usage rate cards", () => {
  it("lists rate cards", async () => {
    const { body } = await run(`{ rateCards { nodes { id version effectiveFrom status items } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).rateCards.nodes[0]).toMatchObject({
      id: "rc-1", version: 1, effectiveFrom: "2026-01-01", status: "active", items: { api_calls: 0.001 },
    });
  });

  it("createRateCard POSTs a draft card and activateRateCard activates it", async () => {
    const create = await run(
      `mutation($input: CreateRateCardInput!) { createRateCard(input: $input) { id status version } }`,
      { input: { version: 2, effectiveFrom: "2026-08-01", items: { api_calls: 0.002 } } },
    );
    expect(create.body?.errors).toBeUndefined();
    expect((create.body?.data as any).createRateCard).toMatchObject({ id: "rc-new", status: "draft", version: 2 });
    const post = create.requests.find((r) => r.method === "POST" && r.path === "/api/v1/rate-cards");
    expect(post?.body).toMatchObject({ version: 2, effective_from: "2026-08-01", items: { api_calls: 0.002 } });

    const activate = await run(`mutation { activateRateCard(id: "rc-new") { id status } }`);
    expect(activate.body?.errors).toBeUndefined();
    expect((activate.body?.data as any).activateRateCard).toMatchObject({ id: "rc-new", status: "active" });
    expect(activate.requests.some((r) => r.method === "POST" && r.path === "/api/v1/rate-cards/rc-new/activate")).toBe(true);
  });
});

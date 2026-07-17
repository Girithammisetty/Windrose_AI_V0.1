import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** chart-service double: just enough for the dashboard-workspace lookup
 * createReportSubscription performs before calling notification-service. */
function chart() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/dashboards/dash-1" && req.method === "GET") {
      return { status: 200, body: { data: { id: "dash-1", workspace_id: "ws-9", name: "Claims overview" } } };
    }
    if (req.path === "/api/v1/dashboards/dash-no-ws" && req.method === "GET") {
      return { status: 200, body: { data: { id: "dash-no-ws", name: "No workspace" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

/** notification-service double: report subscription CRUD + trigger, all
 * responses using notification-service's real {"data": ...} envelope. */
function notification() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/reports" && req.method === "POST") {
      return {
        status: 201,
        body: {
          data: {
            id: "rep-new", workspace_id: req.body.workspace_id, dashboard_id: req.body.dashboard_id,
            name: req.body.name, recipients: req.body.recipients, cadence: req.body.cadence,
            send_hour: req.body.send_hour ?? 8, send_weekday: req.body.send_weekday ?? null,
            timezone: req.body.timezone ?? "UTC", format: req.body.format ?? "html",
            enabled: req.body.enabled ?? true, created_by: "manager@demo.windrose",
            created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/reports" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "rep-1", workspace_id: "ws-9", dashboard_id: "dash-1", name: "Weekly claims",
              recipients: ["manager@demo.windrose"], cadence: "weekly", send_hour: 8, send_weekday: 1,
              timezone: "UTC", format: "html", enabled: true, created_by: "manager@demo.windrose",
              created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" },
          ],
          page: { next_cursor: null, has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/reports/rep-1" && req.method === "PATCH") {
      return {
        status: 200,
        body: { data: { id: "rep-1", workspace_id: "ws-9", dashboard_id: "dash-1",
          name: req.body.name ?? "Weekly claims", recipients: req.body.recipients ?? ["manager@demo.windrose"],
          cadence: req.body.cadence ?? "weekly", send_hour: req.body.send_hour ?? 8,
          send_weekday: req.body.send_weekday ?? 1, timezone: "UTC", format: "html",
          enabled: req.body.enabled ?? true, created_by: "manager@demo.windrose",
          created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" } },
      };
    }
    if (req.path === "/api/v1/reports/rep-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    if (req.path === "/api/v1/reports/rep-1/trigger" && req.method === "POST") {
      return { status: 202 };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

function bothServices(chartDbl: ReturnType<typeof chart>, notifDbl: ReturnType<typeof notification>) {
  return (async (...args: Parameters<typeof fetch>) => {
    const [input, init] = args;
    const url = typeof input === "string" ? input : (input as Request).url;
    if (url.startsWith(cfg.services.chart)) return chartDbl.fetchImpl(input, init);
    return notifDbl.fetchImpl(input, init);
  }) as typeof fetch;
}

describe("report subscription resolvers (notification-service passthrough, JWT forwarded)", () => {
  it("createReportSubscription resolves workspace_id from the target dashboard, not the caller's claim", async () => {
    const server = makeApolloServer(cfg);
    const chartDbl = chart();
    const notifDbl = notification();
    const ctx = await makeTestContext(bothServices(chartDbl, notifDbl), {
      sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-OTHER",
    });
    const res = await server.executeOperation(
      {
        query: `mutation($input: CreateReportSubscriptionInput!, $k: String!) {
          createReportSubscription(input: $input, idempotencyKey: $k) {
            id urn dashboardId workspaceId name recipients cadence sendHour format enabled
          }
        }`,
        variables: {
          input: { dashboardId: "dash-1", name: "Weekly claims", recipients: ["manager@demo.windrose"], cadence: "weekly", sendWeekday: 1 },
          k: "idem-r1",
        },
      },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const sub = (body?.data as any).createReportSubscription;
    expect(sub).toMatchObject({
      id: "rep-new", dashboardId: "dash-1", workspaceId: "ws-9", // from the dashboard, not "ws-OTHER"
      name: "Weekly claims", recipients: ["manager@demo.windrose"], cadence: "weekly", enabled: true,
      urn: "wr:t-42:notification:report_subscription/rep-new",
    });
    const post = notifDbl.requests.find((r) => r.method === "POST" && r.path === "/api/v1/reports");
    expect(post?.body.workspace_id).toBe("ws-9");
    expect(post?.headers["idempotency-key"]).toBe("idem-r1");
    expect(post?.headers["authorization"]).toMatch(/^Bearer /);
  });

  it("createReportSubscription fails closed when the target dashboard has no workspace_id", async () => {
    const server = makeApolloServer(cfg);
    const chartDbl = chart();
    const notifDbl = notification();
    const ctx = await makeTestContext(bothServices(chartDbl, notifDbl));
    const res = await server.executeOperation(
      {
        query: `mutation($input: CreateReportSubscriptionInput!) { createReportSubscription(input: $input) { id } }`,
        variables: { input: { dashboardId: "dash-no-ws", name: "x", recipients: ["a@b.com"], cadence: "daily" } },
      },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
    expect(notifDbl.requests.find((r) => r.method === "POST" && r.path === "/api/v1/reports")).toBeUndefined();
  });

  it("reportSubscriptions lists, optionally filtered by dashboardId", async () => {
    const server = makeApolloServer(cfg);
    const notifDbl = notification();
    const ctx = await makeTestContext(notifDbl.fetchImpl);
    const res = await server.executeOperation(
      { query: `{ reportSubscriptions(dashboardId: "dash-1") { nodes { id name cadence sendWeekday } pageInfo { hasMore } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).reportSubscriptions;
    expect(conn.nodes).toEqual([{ id: "rep-1", name: "Weekly claims", cadence: "weekly", sendWeekday: 1 }]);
    expect(conn.pageInfo.hasMore).toBe(false);
    const get = notifDbl.requests.find((r) => r.method === "GET" && r.path === "/api/v1/reports");
    expect(get?.search.get("dashboard_id")).toBe("dash-1");
  });

  it("pauseReportSubscription PATCHes enabled=false, deleteReportSubscription returns true on 204", async () => {
    const server = makeApolloServer(cfg);
    const notifDbl = notification();
    const ctx = await makeTestContext(notifDbl.fetchImpl);

    const paused = await server.executeOperation(
      { query: `mutation { pauseReportSubscription(id: "rep-1", paused: true) { id enabled } }` },
      { contextValue: ctx },
    );
    const pausedBody = paused.body.kind === "single" ? paused.body.singleResult : null;
    expect(pausedBody?.errors).toBeUndefined();
    const patch = notifDbl.requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/reports/rep-1");
    expect(patch?.body).toEqual({ enabled: false });

    const del = await server.executeOperation(
      { query: `mutation { deleteReportSubscription(id: "rep-1") }` },
      { contextValue: ctx },
    );
    const delBody = del.body.kind === "single" ? del.body.singleResult : null;
    expect(delBody?.errors).toBeUndefined();
    expect((delBody?.data as any).deleteReportSubscription).toBe(true);
  });

  it("triggerReportSubscription POSTs /trigger and returns true on 202 (real Temporal Schedule.Trigger)", async () => {
    const server = makeApolloServer(cfg);
    const notifDbl = notification();
    const ctx = await makeTestContext(notifDbl.fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { triggerReportSubscription(id: "rep-1") }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).triggerReportSubscription).toBe(true);
    expect(notifDbl.requests.find((r) => r.method === "POST" && r.path === "/api/v1/reports/rep-1/trigger")).toBeDefined();
  });
});

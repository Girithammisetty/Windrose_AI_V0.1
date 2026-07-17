import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** chart-service double: chart-type catalog + dashboard/chart authoring CRUD +
 * unsaved-spec preview. All responses use the {"data": <view>} envelope. */
function chart() {
  return mockFetch((req: CapturedRequest) => {
    // chart-type catalog (snake_case fields per domain.ChartType).
    if (req.path === "/api/v1/chart-types" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { name: "line_chart", family: "axis", data_class: "query", required_fields: ["x", "y"],
              config_schema: { type: "object", required: ["x", "y"] } },
            { name: "metric_chart", family: "metric", data_class: "dataset", required_fields: [],
              config_schema: { type: "object" } },
          ],
        },
      };
    }
    // create dashboard (chart-service serializes the label as `name`).
    if (req.path === "/api/v1/dashboards" && req.method === "POST") {
      return {
        status: 201,
        body: { data: { id: "dash-new", workspace_id: req.body.workspace_id, name: req.body.name,
          module: req.body.module, description: req.body.description, layout: [], meta: {},
          tags: req.body.tags ?? [], status: "active", archived: false,
          created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" } },
      };
    }
    // update dashboard
    if (req.path === "/api/v1/dashboards/dash-1" && req.method === "PATCH") {
      return { status: 200, body: { data: { id: "dash-1", workspace_id: "ws-9", name: req.body.name ?? "D1",
        module: "insights", tags: req.body.tags ?? [], archived: false } } };
    }
    // delete dashboard
    if (req.path === "/api/v1/dashboards/dash-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    // list dashboards, honoring filter[archived] (chart-service strict equality)
    if (req.path === "/api/v1/dashboards" && req.method === "GET") {
      const archived = req.search.get("filter[archived]") === "true";
      return {
        status: 200,
        body: {
          data: archived
            ? [{ id: "dash-old", workspace_id: "ws-9", name: "Retired dashboard", module: "insights",
                 archived: true, created_at: "2026-01-01T00:00:00Z" }]
            : [{ id: "dash-1", workspace_id: "ws-9", name: "Live dashboard", module: "insights",
                 archived: false, created_at: "2026-01-01T00:00:00Z" }],
          page: { has_more: false },
        },
      };
    }
    // archive / restore dashboard
    if (req.path === "/api/v1/dashboards/dash-1/archive" && req.method === "POST") {
      return { status: 200, body: { data: { id: "dash-1", workspace_id: "ws-9", name: "Live dashboard", archived: true } } };
    }
    if (req.path === "/api/v1/dashboards/dash-old/restore" && req.method === "PATCH") {
      return { status: 200, body: { data: { id: "dash-old", workspace_id: "ws-9", name: "Retired dashboard", archived: false } } };
    }
    // create chart on a dashboard (chartView shape)
    if (req.path === "/api/v1/dashboards/dash-1/charts" && req.method === "POST") {
      return {
        status: 201,
        body: { data: { id: "chart-new", dashboard_id: "dash-1", name: req.body.name,
          chart_type: req.body.chart_type, description: req.body.description ?? "",
          config: req.body.config, display_meta: req.body.display_meta ?? {},
          sources: req.body.sources ?? [], chart_version: 1, custom: true, config_status: "ok",
          created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" } },
      };
    }
    // update chart
    if (req.path === "/api/v1/charts/chart-1" && req.method === "PATCH") {
      return { status: 200, body: { data: { id: "chart-1", dashboard_id: "dash-1",
        name: req.body.name ?? "C1", chart_type: req.body.chart_type ?? "line_chart",
        config: req.body.config ?? { x: { dimension: "d" } }, display_meta: {}, sources: [],
        chart_version: 2 } } };
    }
    // delete chart
    if (req.path === "/api/v1/charts/chart-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    // preview an unsaved spec (ShapedResult under `data`). network-family
    // charts shape into {nodes, edges} under `graph` instead of rows/columns
    // (chart-service Shape's FamilyNetwork branch).
    if (req.path === "/api/v1/charts/preview" && req.method === "POST") {
      if (req.body.chart_type === "network_chart") {
        return { status: 200, body: { data: { chart_id: "", chart_type: "network_chart",
          chart_version: 0, aggregated: true, columns: [],
          graph: { nodes: [{ id: "a" }, { id: "b" }], edges: [{ from: "a", to: "b" }] },
          row_count: 2, truncated: false, resolved_at: "2026-07-11T00:00:00Z" } } };
      }
      return { status: 200, body: { data: { chart_id: "", chart_type: req.body.chart_type,
        chart_version: 0, aggregated: true, columns: ["x", "y"], rows: [["a", 1], ["b", 2]],
        row_count: 2, truncated: false, resolved_at: "2026-07-11T00:00:00Z" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("chart authoring resolvers (chart-service passthrough, JWT forwarded)", () => {
  it("maps the chart-type catalog (snake→camel, required + schema)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ chartTypes { name family dataClass requiredFields configSchema } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const types: any[] = (body?.data as any).chartTypes;
    expect(types.map((t) => t.name).sort()).toEqual(["line_chart", "metric_chart"]);
    const line = types.find((t) => t.name === "line_chart");
    expect(line).toMatchObject({ family: "axis", dataClass: "query", requiredFields: ["x", "y"] });
    expect(line.configSchema).toEqual({ type: "object", required: ["x", "y"] });
    expect(requests[0]?.headers["authorization"]).toMatch(/^Bearer /);
  });

  it("createDashboard sources workspace_id from the JWT claim and forwards the idempotency key", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-9" });
    const res = await server.executeOperation(
      { query: `mutation($input: CreateDashboardInput!, $k: String!) { createDashboard(input: $input, idempotencyKey: $k) { id urn title module } }`,
        variables: { input: { name: "Q3 Claims", module: "insights", tags: ["a"] }, k: "idem-d1" } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const dash = (body?.data as any).createDashboard;
    // `title` resolves from the chart-service `name` field (fallback mapping).
    expect(dash).toMatchObject({ id: "dash-new", title: "Q3 Claims", module: "insights",
      urn: "wr:t-42:chart:dashboard/dash-new" });
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/dashboards");
    expect(post?.body.workspace_id).toBe("ws-9");
    expect(post?.body.module).toBe("insights");
    expect(post?.headers["idempotency-key"]).toBe("idem-d1");
  });

  it("createDashboard fails closed (VALIDATION_FAILED) when the token carries no workspace", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"] });
    const res = await server.executeOperation(
      { query: `mutation($input: CreateDashboardInput!) { createDashboard(input: $input) { id } }`,
        variables: { input: { name: "No WS", module: "insights" } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
    // Never reached the downstream.
    expect(requests.find((r) => r.method === "POST" && r.path === "/api/v1/dashboards")).toBeUndefined();
  });

  it("updateDashboard PATCHes and deleteDashboard returns true on 204", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);
    const up = await server.executeOperation(
      { query: `mutation($input: UpdateDashboardInput!, $k: String!) { updateDashboard(id: "dash-1", input: $input, idempotencyKey: $k) { id title } }`,
        variables: { input: { name: "Renamed" }, k: "idem-d2" } },
      { contextValue: ctx },
    );
    const upBody = up.body.kind === "single" ? up.body.singleResult : null;
    expect(upBody?.errors).toBeUndefined();
    expect((upBody?.data as any).updateDashboard).toMatchObject({ id: "dash-1", title: "Renamed" });
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/dashboards/dash-1");
    expect(patch?.body.name).toBe("Renamed");
    expect(patch?.headers["idempotency-key"]).toBe("idem-d2");

    const del = await server.executeOperation(
      { query: `mutation { deleteDashboard(id: "dash-1") }` },
      { contextValue: ctx },
    );
    const delBody = del.body.kind === "single" ? del.body.singleResult : null;
    expect(delBody?.errors).toBeUndefined();
    expect((delBody?.data as any).deleteDashboard).toBe(true);
  });

  it("dashboards defaults to live-only; archivedDashboards passes filter[archived]=true", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);

    const live = await server.executeOperation(
      { query: `{ dashboards(workspaceId: "ws-9") { nodes { id archived } } }` },
      { contextValue: ctx },
    );
    const liveBody = live.body.kind === "single" ? live.body.singleResult : null;
    expect(liveBody?.errors).toBeUndefined();
    expect((liveBody?.data as any).dashboards.nodes).toEqual([{ id: "dash-1", archived: false }]);

    const archived = await server.executeOperation(
      { query: `{ archivedDashboards(workspaceId: "ws-9") { nodes { id archived } } }` },
      { contextValue: ctx },
    );
    const archivedBody = archived.body.kind === "single" ? archived.body.singleResult : null;
    expect(archivedBody?.errors).toBeUndefined();
    expect((archivedBody?.data as any).archivedDashboards.nodes).toEqual([{ id: "dash-old", archived: true }]);
    const archivedReq = requests.find((r) => r.method === "GET" && r.path === "/api/v1/dashboards" && r.search.get("filter[archived]") === "true");
    expect(archivedReq).toBeDefined();
  });

  it("archiveDashboard POSTs /archive and restoreDashboard PATCHes /restore", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);

    const archive = await server.executeOperation(
      { query: `mutation { archiveDashboard(id: "dash-1") { id archived } }` },
      { contextValue: ctx },
    );
    const archiveBody = archive.body.kind === "single" ? archive.body.singleResult : null;
    expect(archiveBody?.errors).toBeUndefined();
    expect((archiveBody?.data as any).archiveDashboard).toMatchObject({ id: "dash-1", archived: true });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/dashboards/dash-1/archive")).toBe(true);

    const restore = await server.executeOperation(
      { query: `mutation { restoreDashboard(id: "dash-old") { id archived } }` },
      { contextValue: ctx },
    );
    const restoreBody = restore.body.kind === "single" ? restore.body.singleResult : null;
    expect(restoreBody?.errors).toBeUndefined();
    expect((restoreBody?.data as any).restoreDashboard).toMatchObject({ id: "dash-old", archived: false });
    expect(requests.some((r) => r.method === "PATCH" && r.path === "/api/v1/dashboards/dash-old/restore")).toBe(true);
  });

  it("createChart forwards snake_case body + mapped sources and reads config back", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($input: CreateChartInput!, $k: String!) { createChart(input: $input, idempotencyKey: $k) { id chartType config sources } }`,
        variables: { input: { dashboardId: "dash-1", name: "Trend", chartType: "line_chart",
          config: { x: { dimension: "month" }, y: [{ measure: "amount", agg_fn: "sum" }] },
          sources: [{ position: 0, sourceType: "saved_query", sourceUrn: "wr:t:chart:query/q1" }] }, k: "idem-c1" } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const c = (body?.data as any).createChart;
    expect(c).toMatchObject({ id: "chart-new", chartType: "line_chart" });
    expect(c.config).toEqual({ x: { dimension: "month" }, y: [{ measure: "amount", agg_fn: "sum" }] });
    expect(c.sources).toEqual([{ position: 0, source_type: "saved_query", source_urn: "wr:t:chart:query/q1" }]);
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/dashboards/dash-1/charts");
    expect(post?.body.chart_type).toBe("line_chart");
    expect(post?.body.sources).toEqual([{ position: 0, source_type: "saved_query", source_urn: "wr:t:chart:query/q1" }]);
    expect(post?.headers["idempotency-key"]).toBe("idem-c1");
  });

  it("updateChart PATCHes chart_type/config and deleteChart returns true on 204", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);
    const up = await server.executeOperation(
      { query: `mutation($input: UpdateChartInput!) { updateChart(id: "chart-1", input: $input) { id chartType } }`,
        variables: { input: { chartType: "vertical_bar_chart", config: { x: { dimension: "d" }, y: [] } } } },
      { contextValue: ctx },
    );
    const upBody = up.body.kind === "single" ? up.body.singleResult : null;
    expect(upBody?.errors).toBeUndefined();
    expect((upBody?.data as any).updateChart.chartType).toBe("vertical_bar_chart");
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/charts/chart-1");
    expect(patch?.body.chart_type).toBe("vertical_bar_chart");

    const del = await server.executeOperation(
      { query: `mutation { deleteChart(id: "chart-1") }` },
      { contextValue: ctx },
    );
    const delBody = del.body.kind === "single" ? del.body.singleResult : null;
    expect(delBody?.errors).toBeUndefined();
    expect((delBody?.data as any).deleteChart).toBe(true);
  });

  it("chartPreview posts the unsaved spec (dashboardId ignored) and maps ShapedResult", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = chart();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query($input: CreateChartInput!) { chartPreview(input: $input) { chartType columns rows rowCount truncated } }`,
        variables: { input: { dashboardId: "ignored", name: "n", chartType: "line_chart",
          config: { x: { dimension: "month" }, y: [{ measure: "amount", agg_fn: "sum" }] },
          sources: [{ position: 0, sourceType: "saved_query", sourceUrn: "wr:t:chart:query/q1" }] } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const p = (body?.data as any).chartPreview;
    expect(p).toMatchObject({ chartType: "line_chart", columns: ["x", "y"], rowCount: 2, truncated: false });
    expect(p.rows).toEqual([["a", 1], ["b", 2]]);
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/charts/preview");
    expect(post?.body.chart_type).toBe("line_chart");
    expect(post?.body.sources).toEqual([{ position: 0, source_type: "saved_query", source_urn: "wr:t:chart:query/q1" }]);
    // dashboardId is not part of the preview body.
    expect(post?.body.dashboardId).toBeUndefined();
  });

  it("chartPreview surfaces network-family {nodes,edges} via the graph field", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = chart();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query($input: CreateChartInput!) { chartPreview(input: $input) { chartType graph rows } }`,
        variables: { input: { dashboardId: "ignored", name: "n", chartType: "network_chart",
          config: { nodes: "parent_urn", children: "child_urn" },
          sources: [{ position: 0, sourceType: "saved_query", sourceUrn: "wr:t:chart:query/q1" }] } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const p = (body?.data as any).chartPreview;
    expect(p.graph).toEqual({ nodes: [{ id: "a" }, { id: "b" }], edges: [{ from: "a", to: "b" }] });
    expect(p.rows).toBeNull();
  });
});

/**
 * Contract/fallback fixes:
 *  - Dashboard.charts surfaces authz/data errors instead of silently-empty lists
 *  - Viewer.capsDegraded flags a failed rbac lookup (fail-closed stays)
 *  - Run detail flattens the {data:{run,params,metrics}} envelope
 *  - Proposal maps the real proposal_view fields (tool_id/args) + urn bucketing
 *  - Case accepts both assigned_to_id and assignee_id
 *  - Cost panel reads usd|cost_usd and budget limit|limit_value
 *  - Dataset rowCount reads current_version; Profile reads table.*; per-id fallback
 *  - AgentRun tokenStream uses the real agent_run:<id> topic scheme
 *  - hasMore is false when the downstream returns no cursor (no fake "more")
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { ErrorCode } from "../../src/errors/errors.js";
import { toConnection } from "../../src/pagination.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest, type MockResponse } from "../helpers/mockFetch.js";

const cfg = testConfig();
const single = (res: any) => (res.body.kind === "single" ? res.body.singleResult : null);

describe("Dashboard.charts error surfacing (no silently empty dashboards)", () => {
  const DASH = `{ dashboard(id:"dash-1") { id charts { id data { rows } } } }`;

  function chartService(overrides: (req: CapturedRequest) => MockResponse | null) {
    return mockFetch((req: CapturedRequest): MockResponse => {
      const o = overrides(req);
      if (o) return o;
      if (req.path === "/api/v1/dashboards/dash-1" && req.method === "GET") {
        return { status: 200, body: { data: { id: "dash-1", name: "D1", module: "insights" } } };
      }
      if (req.path === "/api/v1/dashboards/dash-1/charts") {
        return { status: 200, body: { data: [{ id: "c-1", name: "C1", chart_type: "line_chart" }] } };
      }
      if (req.path === "/api/v1/dashboards/dash-1/data") {
        return { status: 200, body: { data: { results: [{ chart_id: "c-1", data: { rows: [[1]], columns: ["y"] } }] } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
  }

  it("rethrows a 403 from the charts list as PERMISSION_DENIED (not an empty list)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = chartService((req) =>
      req.path === "/api/v1/dashboards/dash-1/charts"
        ? { status: 403, body: { error: { code: "PERMISSION_DENIED", message: "no grant", trace_id: "tr-1" } } }
        : null,
    );
    const ctx = await makeTestContext(fetchImpl);
    const body = single(await server.executeOperation({ query: DASH }, { contextValue: ctx }));
    expect(body?.errors?.[0]?.extensions?.code).toBe(ErrorCode.PERMISSION_DENIED);
    expect(body?.errors?.[0]?.extensions?.traceId).toBe("tr-1");
  });

  it("still treats a charts-list 404 as an empty dashboard (tenant masking)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = chartService((req) =>
      req.path === "/api/v1/dashboards/dash-1/charts"
        ? { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } }
        : null,
    );
    const ctx = await makeTestContext(fetchImpl);
    const body = single(await server.executeOperation({ query: DASH }, { contextValue: ctx }));
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).dashboard.charts).toEqual([]);
  });

  it("a failed batch-data call yields per-chart errors, not permanent null data", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = chartService((req) =>
      req.path === "/api/v1/dashboards/dash-1/data"
        ? { status: 403, body: { error: { code: "PERMISSION_DENIED", message: "no data grant", trace_id: "tr-2" } } }
        : null,
    );
    const ctx = await makeTestContext(fetchImpl);
    const body = single(await server.executeOperation({ query: DASH }, { contextValue: ctx }));
    // Chart metadata still resolves; each chart's nullable `data` errors distinctly.
    expect((body?.data as any).dashboard.charts[0].id).toBe("c-1");
    expect((body?.data as any).dashboard.charts[0].data).toBeNull();
    const err = body?.errors?.find((e: any) => e.path?.join(".") === "dashboard.charts.0.data");
    expect(err?.extensions?.code).toBe(ErrorCode.PERMISSION_DENIED);
  });

  it("happy path still hydrates rows via the batch call", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = chartService(() => null);
    const ctx = await makeTestContext(fetchImpl);
    const body = single(await server.executeOperation({ query: DASH }, { contextValue: ctx }));
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).dashboard.charts[0].data.rows).toEqual([[1]]);
  });
});

describe("Viewer.capsDegraded (distinguishable rbac degradation, fail-closed kept)", () => {
  const Q = `{ me { roles capabilities capsDegraded } }`;

  it("is false when rbac answers", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) =>
      req.path === "/api/v1/me/capabilities"
        ? { status: 200, body: { roles: ["Analyst"], capabilities: ["case.case.read"], admin: false } }
        : { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } },
    );
    const ctx = await makeTestContext(fetchImpl);
    const me: any = single(await server.executeOperation({ query: Q }, { contextValue: ctx }))?.data?.me;
    expect(me).toEqual({ roles: ["Analyst"], capabilities: ["case.case.read"], capsDegraded: false });
  });

  it("is true (with fail-closed empty caps) when rbac is down", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch(() => ({ status: 503, body: { error: { code: "UNAVAILABLE", message: "down" } } }));
    const ctx = await makeTestContext(fetchImpl);
    const body = single(await server.executeOperation({ query: Q }, { contextValue: ctx }));
    expect(body?.errors).toBeUndefined();
    expect(body?.data?.me).toEqual({ roles: [], capabilities: [], capsDegraded: true });
  });
});

describe("Run detail envelope flatten ({data:{run,params,metrics}} -> Run)", () => {
  // The REAL experiment-service get_detail shape: run fields nested under `run`,
  // params/metrics as SIBLINGS, metrics keyed by name -> {value, step, logged_at}.
  const RUN_DETAIL = {
    data: {
      run: {
        id: "run-1", urn: "wr:t-42:experiment:run/run-1", experiment_id: "exp-1",
        mlflow_run_id: "mlf-1", name: "baseline-xgb", status: "succeeded",
        status_label: "Succeeded", algorithm: "xgboost", artifact_uri: "s3://x",
        duration_ms: 1200, started_at: "2026-07-10T00:00:00Z", ended_at: "2026-07-10T00:00:02Z",
        error_messages: [], created_at: "2026-07-10T00:00:00Z",
      },
      params: { max_depth: "6", eta: "0.3" },
      params_conflict: [],
      metrics: {
        auc: { value: 0.91, step: 3, logged_at: "2026-07-10T00:00:01Z" },
        loss: { value: 0.12, step: 3, logged_at: "2026-07-10T00:00:01Z" },
      },
      tags: {},
      artifacts: [{ path: "model.pkl", size_bytes: 10, content_type: "application/octet-stream" }],
      input_dataset_urns: [],
      output_dataset_urns: [],
      note: null,
    },
  };

  it("flattens run fields + params as-is + metrics to last value per key (Float)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) =>
      req.path === "/api/v1/runs/run-1"
        ? { status: 200, body: RUN_DETAIL }
        : { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } },
    );
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ run(id:"run-1") { id name status metrics params } }` },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    const run: any = body?.data?.run;
    expect(run.id).toBe("run-1");
    expect(run.name).toBe("baseline-xgb");
    expect(run.status).toBe("SUCCEEDED");
    expect(run.metrics).toEqual({ auc: 0.91, loss: 0.12 }); // {value,step} -> value
    expect(run.params).toEqual({ max_depth: "6", eta: "0.3" }); // as-is
  });

  it("passes an already-flat run payload through untouched (defensive)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) =>
      req.path === "/api/v1/runs/run-2"
        ? { status: 200, body: { data: { id: "run-2", name: "flat", status: "running", metrics: { f1: 0.5 } } } }
        : { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } },
    );
    const ctx = await makeTestContext(fetchImpl);
    const run: any = single(
      await server.executeOperation({ query: `{ run(id:"run-2") { id metrics } }` }, { contextValue: ctx }),
    )?.data?.run;
    expect(run).toEqual({ id: "run-2", metrics: { f1: 0.5 } });
  });
});

describe("Proposal maps real proposal_view fields + resource-urn bucketing", () => {
  it("reads tool_id -> tool and args -> argsDiff (legacy names still accepted)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) =>
      req.path === "/api/v1/proposals"
        ? {
            status: 200,
            body: {
              data: [
                { id: "pr-1", tool_id: "assign_case", args: { assignee: "u-2" }, tier: "write-proposal", status: "pending" },
                { id: "pr-2", tool: "legacy_tool", args_diff: { a: 1 }, status: "pending" },
              ],
              page: { has_more: false },
            },
          }
        : { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } },
    );
    const ctx = await makeTestContext(fetchImpl);
    const nodes: any[] = single(
      await server.executeOperation(
        { query: `{ proposalsInbox(status: PENDING) { nodes { id tool argsDiff } } }` },
        { contextValue: ctx },
      ),
    )?.data?.proposalsInbox?.nodes;
    expect(nodes.find((n) => n.id === "pr-1")).toEqual({ id: "pr-1", tool: "assign_case", argsDiff: { assignee: "u-2" } });
    expect(nodes.find((n) => n.id === "pr-2")).toEqual({ id: "pr-2", tool: "legacy_tool", argsDiff: { a: 1 } });
  });

  it("Case.proposals sends filter[resource_urn] and buckets on the RETURNED resource_urn (affected_urns fallback)", async () => {
    const server = makeApolloServer(cfg);
    const caseUrn = "wr:t-42:case:case/case-1";
    const { fetchImpl, requests } = mockFetch((req) => {
      if (req.path === "/api/v1/cases/case-1") {
        return { status: 200, body: { id: "case-1", case_number: 7, status: "in_progress" } };
      }
      if (req.path === "/api/v1/proposals") {
        return {
          status: 200,
          body: {
            data: [
              { id: "pr-a", tool_id: "t1", status: "pending", resource_urn: caseUrn },
              { id: "pr-b", tool_id: "t2", status: "pending", resource_urn: "wr:t-42:case:case/other" },
              { id: "pr-c", tool_id: "t3", status: "pending", affected_urns: [caseUrn] }, // no resource_urn yet
            ],
            page: { has_more: false },
          },
        };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ case(id:"case-1") { proposals { id } } }` },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).case.proposals.map((p: any) => p.id).sort()).toEqual(["pr-a", "pr-c"]);
    const call = requests.find((r) => r.path === "/api/v1/proposals");
    expect(call?.search.get("filter[resource_urn]")).toBe(caseUrn);
  });

  it("AgentRun reads run_view usage {input_tokens, output_tokens} into tokenUsage", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) =>
      req.path === "/api/v1/runs/ar-1"
        ? {
            status: 200,
            body: { data: { id: "ar-1", session_id: "s-1", agent_key: "triage", status: "succeeded",
              principal_type: "user", usage: { input_tokens: 812, output_tokens: 240, model: "m", deployment: "d" }, error: null } },
          }
        : { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } },
    );
    const ctx = await makeTestContext(fetchImpl);
    const run: any = single(
      await server.executeOperation(
        { query: `{ agentRun(id:"ar-1") { id status costUsd tokenUsage { inputTokens outputTokens } tokenStream { topics } } }` },
        { contextValue: ctx },
      ),
    )?.data?.agentRun;
    expect(run.tokenUsage).toEqual({ inputTokens: 812, outputTokens: 240 });
    expect(run.costUsd).toBeNull(); // run_view carries no cost today
    // Real realtime-hub scheme: agent_run:<run_id>
    expect(run.tokenStream.topics).toEqual(["agent_run:ar-1"]);
  });
});

describe("Case assignee id: both CRUD (assigned_to_id) and search (assignee_id) names", () => {
  function ids(users: { id: string; email: string }[]) {
    return mockFetch((req) => {
      if (req.path === "/api/v1/cases/case-crud") {
        return { status: 200, body: { id: "case-crud", assigned_to_id: "u-1", status: "in_progress" } };
      }
      if (req.path === "/api/v1/cases/case-search") {
        return { status: 200, body: { id: "case-search", assignee_id: "u-1", status: "in_progress" } };
      }
      // The userById loader hydrates Case.assignee via identity's member-safe
      // /users/profiles batch endpoint (no admin scope) — NOT the admin
      // /api/v1/users directory listing. Mock the endpoint the loader calls.
      if (req.path === "/api/v1/users/profiles") {
        return { status: 200, body: { data: users, page: { has_more: false } } };
      }
      if (req.path === "/api/v1/proposals") {
        return { status: 200, body: { data: [], page: { has_more: false } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
  }

  it("hydrates the assignee from either field name", async () => {
    const server = makeApolloServer(cfg);
    for (const id of ["case-crud", "case-search"]) {
      const { fetchImpl } = ids([{ id: "u-1", email: "ann@x.com" }]);
      const ctx = await makeTestContext(fetchImpl);
      const body = single(
        await server.executeOperation(
          { query: `{ case(id:"${id}") { assignee { email } } }` },
          { contextValue: ctx },
        ),
      );
      expect(body?.errors).toBeUndefined();
      expect((body?.data as any).case.assignee.email).toBe("ann@x.com");
    }
  });

  it("drops users the downstream returned that were NOT requested (no wrong hydration)", async () => {
    const server = makeApolloServer(cfg);
    // identity ignores filter[id] and returns an arbitrary other user.
    const { fetchImpl } = ids([{ id: "u-999", email: "stranger@x.com" }]);
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ case(id:"case-crud") { assignee { email } } }` },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).case.assignee).toBeNull(); // never "stranger"
  });
});

describe("Dataset/Profile field nesting + per-id fallback (dataset-service contract)", () => {
  it("reads rowCount from current_version.row_count and profile counts from table.*", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) => {
      if (req.path === "/api/v1/datasets/ds-1" && req.method === "GET") {
        return {
          status: 200,
          body: { data: { id: "ds-1", name: "claims", status: "ready", tags: [],
            current_version: { version_no: 3, row_count: 120000, bytes: 9, breaking_change: false, profile_status: "COMPLETED" } } },
        };
      }
      if (req.path === "/api/v1/datasets/ds-1/profile") {
        return {
          status: 200,
          body: { data: { profile_id: "p-1", status: "COMPLETED", version_no: 3,
            table: { row_count: 120000, column_count: 42, bytes: 9, duplicate_row_pct: 0.1 },
            columns: [], alerts: [], full_json_url: "http://x/full.json", html_report_url: "http://x/report.html" } },
        };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ dataset(id:"ds-1") { rowCount profile { rowCount columnCount fullJsonUrl htmlReportUrl } } }` },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).dataset).toEqual({
      rowCount: 120000,
      profile: { rowCount: 120000, columnCount: 42, fullJsonUrl: "http://x/full.json", htmlReportUrl: "http://x/report.html" },
    });
  });

  it("Case.sourceDataset falls back to bounded per-id GETs when the list ignores filter[id]", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = mockFetch((req) => {
      if (req.path === "/api/v1/cases/case-1") {
        return { status: 200, body: { id: "case-1", status: "in_progress", dataset_urn: "wr:t-42:dataset:dataset/ds-9" } };
      }
      // dataset-service list IGNORES filter[id]: returns unrelated rows.
      if (req.path === "/api/v1/datasets" && req.method === "GET") {
        return { status: 200, body: { data: [{ id: "ds-other", name: "unrelated" }], page: { has_more: false } } };
      }
      if (req.path === "/api/v1/datasets/ds-9") {
        return { status: 200, body: { data: { id: "ds-9", name: "claims-2026", current_version: { row_count: 7 } } } };
      }
      if (req.path === "/api/v1/proposals") {
        return { status: 200, body: { data: [], page: { has_more: false } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ case(id:"case-1") { sourceDataset { id name rowCount } } }` },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).case.sourceDataset).toEqual({ id: "ds-9", name: "claims-2026", rowCount: 7 });
    // one filtered list attempt + one per-id fallback GET
    expect(requests.filter((r) => r.path === "/api/v1/datasets").length).toBe(1);
    expect(requests.filter((r) => r.path === "/api/v1/datasets/ds-9").length).toBe(1);
  });

  it("a profile 5xx surfaces as SERVICE_UNAVAILABLE (outage != not-profiled)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) => {
      if (req.path === "/api/v1/datasets/ds-1") {
        return { status: 200, body: { data: { id: "ds-1", name: "claims", tags: [] } } };
      }
      if (req.path === "/api/v1/datasets/ds-1/profile") {
        return { status: 503, body: { error: { code: "UNAVAILABLE", message: "down", trace_id: "tr-p" } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ dataset(id:"ds-1") { id profile { rowCount } } }` },
        { contextValue: ctx },
      ),
    );
    expect((body?.data as any).dataset.profile).toBeNull();
    const err = body?.errors?.find((e: any) => e.path?.join(".") === "dataset.profile");
    expect(err?.extensions?.code).toBe(ErrorCode.SERVICE_UNAVAILABLE);
  });

  it("a profile 404 still reads as not-profiled (null, no error)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) => {
      if (req.path === "/api/v1/datasets/ds-1") {
        return { status: 200, body: { data: { id: "ds-1", name: "claims", tags: [] } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        { query: `{ dataset(id:"ds-1") { id profile { rowCount } } }` },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).dataset.profile).toBeNull();
  });
});

describe("Cost panel field names (usage-service RollupRow + budget views)", () => {
  it("reads usd|cost_usd on rows and limit|limit_value on budget states", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req) => {
      if (req.path === "/api/v1/reports/usage") {
        return {
          status: 200,
          body: {
            data: [
              { meter_key: "llm.tokens", unit: "tokens", quantity: 1000, usd: 1.25 }, // today's name
              { meter_key: "pipeline.runs", unit: "runs", quantity: 3, cost_usd: 0.5 }, // incoming name
            ],
            page: { has_more: false },
          },
        };
      }
      if (req.path === "/api/v1/budget-states") {
        return {
          status: 200,
          body: {
            data: [
              { budget_id: "b-1", window_start: "2026-07-01", consumed: 40, limit: 100, last_threshold: 0,
                action: "block", scope: { tenant_id: "t-42", workspace_id: "ws-9" } },
              { budget_id: "b-2", window_start: "2026-07-01", consumed: 9, limit_value: 10, last_threshold: 80,
                scope: "workspace/ws-9" },
            ],
            page: { has_more: false },
          },
        };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        {
          query: `query($w: ID!, $f: Date!, $t: Date!) { workspaceCostPanel(workspaceId: $w, from: $f, to: $t) {
            rows { meterKey quantity costUsd }
            budgetStates { scope consumed limit lastThreshold }
          } }`,
          variables: { w: "ws-9", f: "2026-06-11", t: "2026-07-11" },
        },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    const panel: any = body?.data?.workspaceCostPanel;
    expect(panel.rows).toEqual([
      { meterKey: "llm.tokens", quantity: 1000, costUsd: 1.25 },
      { meterKey: "pipeline.runs", quantity: 3, costUsd: 0.5 },
    ]);
    expect(panel.budgetStates).toEqual([
      { scope: "workspace/ws-9", consumed: 40, limit: 100, lastThreshold: 0 },
      { scope: "workspace/ws-9", consumed: 9, limit: 10, lastThreshold: 80 },
    ]);
  });
});

describe("bulkAssignCases: real partial-failure result (no fake success)", () => {
  function bulk(body: MockResponse["body"], status = 200) {
    return mockFetch((req: CapturedRequest) => {
      if (req.path === "/api/v1/cases/bulk" && req.method === "POST") {
        return { status, body };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
  }

  it("posts operation=assign with the real case ids + assignee, maps succeeded/failed", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = bulk({
      succeeded: ["case-1", "case-2"],
      failed: [{ id: "case-3", code: "NOT_FOUND", message: "case not found" }],
    });
    const ctx = await makeTestContext(fetchImpl);
    const body = single(
      await server.executeOperation(
        {
          query: `mutation($caseIds:[ID!]!,$assigneeId:ID!){ bulkAssignCases(caseIds:$caseIds, assigneeId:$assigneeId) { succeededIds failed { caseId code message } } }`,
          variables: { caseIds: ["case-1", "case-2", "case-3"], assigneeId: "u-1" },
        },
        { contextValue: ctx },
      ),
    );
    expect(body?.errors).toBeUndefined();
    const r = (body?.data as any).bulkAssignCases;
    expect(r.succeededIds).toEqual(["case-1", "case-2"]);
    expect(r.failed).toEqual([{ caseId: "case-3", code: "NOT_FOUND", message: "case not found" }]);
    const post = requests.find((q) => q.path === "/api/v1/cases/bulk");
    expect(post?.body).toEqual({
      operation: "assign",
      case_ids: ["case-1", "case-2", "case-3"],
      params: { assignee_id: "u-1" },
    });
  });

  it("never reports success when the downstream returns zero succeeded ids", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = bulk(
      { succeeded: [], failed: [{ id: "case-1", code: "FORBIDDEN", message: "denied" }] },
      422,
    );
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      {
        query: `mutation($caseIds:[ID!]!,$assigneeId:ID!){ bulkAssignCases(caseIds:$caseIds, assigneeId:$assigneeId) { succeededIds } }`,
        variables: { caseIds: ["case-1"], assigneeId: "u-1" },
      },
      { contextValue: ctx },
    );
    const body = single(res);
    // The 422 propagates as a real GraphQL error — never a silent/fake success.
    expect(body?.errors?.length).toBeGreaterThan(0);
  });
});

describe("pagination honesty: no hasMore without a cursor to fetch it", () => {
  it("derives hasMore=false when the downstream flags has_more but gives no cursor", () => {
    const conn = toConnection({ data: [{ id: "a" }], page: { next_cursor: null, has_more: true } }, (x) => x);
    expect(conn.pageInfo).toEqual({ nextCursor: null, hasMore: false });
  });

  it("keeps hasMore=true when a cursor exists", () => {
    const conn = toConnection({ data: [{ id: "a" }], page: { next_cursor: "n1", has_more: true } }, (x) => x);
    expect(conn.pageInfo).toEqual({ nextCursor: "n1", hasMore: true });
  });
});

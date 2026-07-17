/**
 * Tier 4a: data-plane secondary CRUD/lifecycle resolvers.
 *  - query-service: saved-query authoring (create/update/delete/versions) +
 *    execution history (list/get/cancel) + stats.
 *  - ingestion-service: schedules CRUD/pause/resume/run_now, ingestion
 *    cancel/retry/reingest, connection PATCH + preview.
 *  - dataset-service: consumers, versions, similar, re-profile.
 *  - semantic-service: verified queries lifecycle (four-eyes) + bootstrap.
 *  - pipeline-orchestrator: run terminate/retry/manifest + template lifecycle.
 * Mocking is at the fetch boundary; real master envelopes on both sides.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();
const single = (res: any) => (res.body.kind === "single" ? res.body.singleResult : null);

// ---------------------------------------------------------------------------
// query-service
// ---------------------------------------------------------------------------

function queryService() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/queries" && req.method === "POST") {
      if ((req.body.module_names ?? []).length === 0) {
        return { status: 422, body: { error: { code: "VALIDATION_FAILED",
          message: "module_names must contain at least one module", trace_id: "tr" } } };
      }
      return { status: 201, body: { data: {
        id: "q-new", workspace_id: req.body.workspace_id ?? null, name: req.body.name,
        description: req.body.description ?? "", current_version_no: 1, version_no: 1,
        tags: req.body.tags ?? [], module_names: req.body.module_names,
        sql_text: req.body.sql_text, variables: req.body.variables ?? [],
        created_by: "u-1", created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/queries/q-1" && req.method === "PATCH") {
      return { status: 200, body: { data: {
        id: "q-1", name: req.body.name ?? "old", current_version_no: 3, version_no: 3,
        module_names: req.body.module_names ?? ["claims"], sql_text: req.body.sql_text,
        tags: [], created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/queries/q-1" && req.method === "DELETE") return { status: 204 };
    if (req.path === "/api/v1/queries/q-1/versions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "v-2", saved_query_id: "q-1", version_no: 2, sql_text: "SELECT 2",
          variables: [{ name: "s", type: "string" }], dataset_refs: [], created_by: "u-1",
          created_at: "2026-07-12T00:00:00Z" },
        { id: "v-1", saved_query_id: "q-1", version_no: 1, sql_text: "SELECT 1",
          variables: [], dataset_refs: [], created_by: "u-1", created_at: "2026-07-11T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/executions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { execution_id: "e-1", id: "e-1", status: "running", engine: "duckdb", cache_hit: false,
          saved_query_id: "q-1", query_version_no: 2, created_by: "u-1",
          created_at: "2026-07-12T01:00:00Z", started_at: "2026-07-12T01:00:01Z",
          stats: { actual_scan_bytes: 1024, result_rows: null, duration_ms: null } },
      ], page: { next_cursor: "c1", has_more: true } } };
    }
    if (req.path === "/api/v1/executions/e-1" && req.method === "GET") {
      return { status: 200, body: { data: {
        execution_id: "e-1", id: "e-1", status: "succeeded", engine: "duckdb",
        sql_text: "SELECT 1", created_at: "2026-07-12T01:00:00Z",
        stats: { actual_scan_bytes: 2048, result_rows: 10, duration_ms: 42 },
      } } };
    }
    if (req.path === "/api/v1/executions/e-1/cancel" && req.method === "POST") {
      return { status: 200, body: { data: {
        execution_id: "e-1", id: "e-1", status: "cancelled", engine: "duckdb",
        created_at: "2026-07-12T01:00:00Z", stats: {},
      } } };
    }
    if (req.path === "/api/v1/stats/queries" && req.method === "GET") {
      return { status: 200, body: { data: { since: "2026-07-05T00:00:00Z", top_queries: [
        { sql_fingerprint: "abc123", executions: 12, total_scan_bytes: 4096, failures: 2, top_user: "u-1" },
      ] } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("saved-query authoring + execution history (query-service passthrough)", () => {
  it("createSavedQuery maps camel input to the snake savedQueryReq and threads the JWT workspace", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = queryService();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-9" });
    const res = await server.executeOperation(
      {
        query: `mutation($input: SavedQueryInput!) {
          createSavedQuery(input: $input) { id name moduleNames versionNo sqlText urn }
        }`,
        variables: { input: {
          name: "top_claims", moduleNames: ["claims"], sqlText: "SELECT :status",
          variables: [{ name: "status", type: "string", required: false, allowedValues: ["open", "closed"] }],
          tags: ["hero"],
        } },
      },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createSavedQuery).toMatchObject({
      id: "q-new", name: "top_claims", moduleNames: ["claims"], versionNo: 1, sqlText: "SELECT :status",
      urn: "wr:t-42:query:query/q-new",
    });
    const post = requests.find((r) => r.path === "/api/v1/queries" && r.method === "POST");
    expect(post?.body).toMatchObject({
      name: "top_claims",
      module_names: ["claims"],
      sql_text: "SELECT :status",
      workspace_id: "ws-9",
      variables: [{ name: "status", type: "string", required: false, allowed_values: ["open", "closed"] }],
    });
  });

  it("createSavedQuery surfaces the service's 422 as VALIDATION_FAILED (no client-side faking)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($input: SavedQueryInput!) { createSavedQuery(input: $input) { id } }`,
        variables: { input: { name: "x", moduleNames: [], sqlText: "SELECT 1" } } },
      { contextValue: ctx },
    );
    expect(single(res)?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
  });

  it("updateSavedQuery PATCHes and returns the bumped immutable version", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { updateSavedQuery(id: "q-1", input: { sqlText: "SELECT 3", name: "renamed" }) { id versionNo } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateSavedQuery).toMatchObject({ id: "q-1", versionNo: 3 });
    const patch = requests.find((r) => r.method === "PATCH");
    expect(patch?.body).toMatchObject({ sql_text: "SELECT 3", name: "renamed" });
    // Absent fields stay absent so the Go handler's nil-pointer semantics hold.
    expect(patch?.body).not.toHaveProperty("module_names");
  });

  it("deleteSavedQuery resolves true on the 204", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { deleteSavedQuery(id: "q-1") }` },
      { contextValue: ctx },
    );
    expect(single(res)?.data).toEqual({ deleteSavedQuery: true });
  });

  it("savedQueryVersions lists the immutable history newest-first", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ savedQueryVersions(queryId: "q-1", first: 10) {
          nodes { id versionNo sqlText } pageInfo { hasMore } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).savedQueryVersions;
    expect(conn.nodes.map((n: any) => n.versionNo)).toEqual([2, 1]);
    expect(conn.nodes[0].sqlText).toBe("SELECT 2");
  });

  it("queryExecutions filters by status + savedQueryId and maps history rows", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ queryExecutions(first: 10, status: "running", savedQueryId: "q-1") {
          nodes { id status engine savedQueryId queryVersionNo scanBytes urn } pageInfo { nextCursor hasMore } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).queryExecutions;
    expect(conn.nodes[0]).toMatchObject({
      id: "e-1", status: "running", engine: "duckdb", savedQueryId: "q-1", queryVersionNo: 2,
      scanBytes: 1024, urn: "wr:t-42:query:execution/e-1",
    });
    expect(conn.pageInfo).toEqual({ nextCursor: "c1", hasMore: true });
    const list = requests.find((r) => r.path === "/api/v1/executions");
    expect(list?.search.get("status")).toBe("running");
    expect(list?.search.get("saved_query_id")).toBe("q-1");
  });

  it("queryExecution hydrates sql_text on the single path; cancelQueryExecution POSTs /cancel", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ queryExecution(id: "e-1") { id sqlText durationMs resultRows } }` },
      { contextValue: ctx },
    );
    expect((single(res)?.data as any).queryExecution).toMatchObject({
      id: "e-1", sqlText: "SELECT 1", durationMs: 42, resultRows: 10,
    });

    const res2 = await server.executeOperation(
      { query: `mutation { cancelQueryExecution(id: "e-1") { id status } }` },
      { contextValue: ctx },
    );
    expect((single(res2)?.data as any).cancelQueryExecution).toMatchObject({ id: "e-1", status: "cancelled" });
    expect(requests.some((r) => r.path === "/api/v1/executions/e-1/cancel" && r.method === "POST")).toBe(true);
  });

  it("queryStats maps the TA rollup rows", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = queryService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ queryStats { since topQueries { sqlFingerprint executions totalScanBytes failures topUser } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).queryStats.topQueries[0]).toEqual({
      sqlFingerprint: "abc123", executions: 12, totalScanBytes: 4096, failures: 2, topUser: "u-1",
    });
  });
});

// ---------------------------------------------------------------------------
// ingestion-service: schedules + lifecycle + connection patch/preview
// ---------------------------------------------------------------------------

const SCHEDULE = {
  id: "sch-1", connection_id: "conn-1",
  ingestion_template: { statement: "SELECT * FROM t", new_dataset: { name: "landed" } },
  cron: "0 6 * * *", interval_seconds: null, timezone: "UTC",
  watermark: null, overlap_policy: "skip", enabled: true,
  workspace_id: "ws-9", last_fired_at: null, next_fire_at: "2026-07-13T06:00:00Z",
  created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
};

function ingestionService() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/schedules" && req.method === "POST") {
      const hasCron = !!req.body.cron;
      const hasInterval = req.body.interval_seconds != null;
      if (hasCron === hasInterval) {
        return { status: 422, body: { error: { code: "VALIDATION_FAILED",
          message: "provide exactly one of cron / interval_seconds", trace_id: "tr" } } };
      }
      return { status: 201, body: { data: { ...SCHEDULE, cron: req.body.cron ?? null,
        interval_seconds: req.body.interval_seconds ?? null } } };
    }
    if (req.path === "/api/v1/schedules" && req.method === "GET") {
      return { status: 200, body: { data: [SCHEDULE], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/schedules/sch-1" && req.method === "PATCH") {
      return { status: 200, body: { data: { ...SCHEDULE, cron: req.body.cron ?? SCHEDULE.cron,
        enabled: req.body.enabled ?? SCHEDULE.enabled } } };
    }
    if (req.path === "/api/v1/schedules/sch-1" && req.method === "DELETE") return { status: 204 };
    if (req.path === "/api/v1/schedules/sch-1/pause" && req.method === "POST") {
      return { status: 200, body: { data: { ...SCHEDULE, enabled: false } } };
    }
    if (req.path === "/api/v1/schedules/sch-1/resume" && req.method === "POST") {
      return { status: 200, body: { data: { ...SCHEDULE, enabled: true } } };
    }
    if (req.path === "/api/v1/schedules/sch-1/run_now" && req.method === "POST") {
      return { status: 200, body: { data: { skipped: false, ingestion_id: "ing-9", buffered: false } } };
    }
    if (req.path === "/api/v1/ingestions/ing-1/cancel" && req.method === "POST") {
      return { status: 200, body: { data: { id: "ing-1", ingestion_mode: "query", status: "cancelled" } } };
    }
    if (req.path === "/api/v1/ingestions/ing-1/retry" && req.method === "POST") {
      return { status: 202, body: { data: { id: "ing-2", ingestion_mode: "query", status: "queued" } } };
    }
    if (req.path === "/api/v1/ingestions/ing-1/reingest" && req.method === "POST") {
      return { status: 202, body: { data: { id: "ing-3", ingestion_mode: "query", status: "created" } } };
    }
    if (req.path === "/api/v1/connections/conn-1" && req.method === "PATCH") {
      return { status: 200, body: { data: {
        id: "conn-1", name: req.body.name ?? "warehouse", connector_type: "postgres",
        config: req.body.config ?? { host: "db" }, secrets: { password: "***" }, secret_set: true,
        traffic_direction: "incoming", tags: [], last_test_status: "ok",
        last_tested_at: "2026-07-12T00:00:00Z",
      } } };
    }
    if (req.path === "/api/v1/connections/conn-1/preview" && req.method === "POST") {
      if (!req.body.table && !req.body.path && !req.body.query) {
        return { status: 422, body: { error: { code: "VALIDATION_FAILED",
          message: "preview target required", trace_id: "tr" } } };
      }
      return { status: 200, body: { data: { columns: ["id", "amount"],
        rows: [{ id: 1, amount: 10.5 }, { id: 2, amount: 20 }] } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("ingestion schedules + lifecycle + connection edit/preview (ingestion-service passthrough)", () => {
  it("createIngestionSchedule maps camel input to the snake ScheduleCreate body", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestionService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      {
        query: `mutation($input: CreateIngestionScheduleInput!) {
          createIngestionSchedule(input: $input) { id cron enabled connectionId urn }
        }`,
        variables: { input: {
          connectionId: "conn-1", ingestionTemplate: { statement: "SELECT 1", new_dataset: { name: "d" } },
          cron: "0 6 * * *", overlapPolicy: "skip",
        } },
      },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createIngestionSchedule).toMatchObject({
      id: "sch-1", cron: "0 6 * * *", enabled: true, connectionId: "conn-1",
      urn: "wr:t-42:ingestion:schedule/sch-1",
    });
    const post = requests.find((r) => r.path === "/api/v1/schedules" && r.method === "POST");
    expect(post?.body).toMatchObject({
      connection_id: "conn-1", cron: "0 6 * * *", overlap_policy: "skip",
      ingestion_template: { statement: "SELECT 1", new_dataset: { name: "d" } },
    });
    expect(post?.body).not.toHaveProperty("interval_seconds");
  });

  it("createIngestionSchedule bubbles the XOR-timing 422 from the service", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = ingestionService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($input: CreateIngestionScheduleInput!) { createIngestionSchedule(input: $input) { id } }`,
        variables: { input: { connectionId: "conn-1", ingestionTemplate: {}, cron: "0 6 * * *", intervalSeconds: 3600 } } },
      { contextValue: ctx },
    );
    expect(single(res)?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
  });

  it("pause/resume/run_now hit the real control routes; run_now returns the fire outcome", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestionService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          pauseIngestionSchedule(id: "sch-1") { enabled }
          resumeIngestionSchedule(id: "sch-1") { enabled }
          runIngestionScheduleNow(id: "sch-1") { skipped ingestionId buffered }
          deleteIngestionSchedule(id: "sch-1")
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect(body?.data).toMatchObject({
      pauseIngestionSchedule: { enabled: false },
      resumeIngestionSchedule: { enabled: true },
      runIngestionScheduleNow: { skipped: false, ingestionId: "ing-9", buffered: false },
      deleteIngestionSchedule: true,
    });
    for (const p of ["pause", "resume", "run_now"]) {
      expect(requests.some((r) => r.path === `/api/v1/schedules/sch-1/${p}` && r.method === "POST")).toBe(true);
    }
  });

  it("cancel/retry/reingest map the ingestion lifecycle routes (retry/reingest return the NEW run)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = ingestionService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          cancelIngestion(id: "ing-1") { id status }
          retryIngestion(id: "ing-1") { id status }
          reingestIngestion(id: "ing-1") { id status }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect(body?.data).toMatchObject({
      cancelIngestion: { id: "ing-1", status: "cancelled" },
      retryIngestion: { id: "ing-2", status: "queued" },
      reingestIngestion: { id: "ing-3", status: "created" },
    });
  });

  it("updateConnection PATCHes only the supplied fields (write-only secrets merge downstream)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestionService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($input: UpdateConnectionInput!) {
          updateConnection(id: "conn-1", input: $input) { id name secretSet lastTestStatus }
        }`,
        variables: { input: { name: "renamed", secrets: { password: "new-pw" } } } },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateConnection).toMatchObject({ id: "conn-1", name: "renamed", secretSet: true });
    const patch = requests.find((r) => r.method === "PATCH");
    expect(patch?.body).toEqual({ name: "renamed", secrets: { password: "new-pw" } });
  });

  it("connectionPreview returns live columns+rows and bubbles the target-required 422", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = ingestionService();
    const ctx = await makeTestContext(fetchImpl);
    const ok = await server.executeOperation(
      { query: `{ connectionPreview(id: "conn-1", input: { query: "SELECT 1", limit: 5 }) { columns rows } }` },
      { contextValue: ctx },
    );
    const body = single(ok);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).connectionPreview.columns).toEqual(["id", "amount"]);
    expect((body?.data as any).connectionPreview.rows).toHaveLength(2);

    const bad = await server.executeOperation(
      { query: `{ connectionPreview(id: "conn-1", input: {}) { columns } }` },
      { contextValue: ctx },
    );
    expect(single(bad)?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
  });
});

// ---------------------------------------------------------------------------
// dataset-service: consumers / versions / similar / re-profile
// ---------------------------------------------------------------------------

function datasetService() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/datasets/ds-1" && req.method === "GET") {
      return { status: 200, body: { data: { id: "ds-1", name: "claims", tags: [],
        current_version: { version_no: 3, row_count: 100 } } } };
    }
    if (req.path === "/api/v1/datasets/ds-1/consumers") {
      return { status: 200, body: { data: { downstream_edges: 4,
        by_service: { query: 3, chart: 1 }, by_activity: { executed: 4 }, truncated: false } } };
    }
    if (req.path === "/api/v1/datasets/ds-1/versions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "dv-3", dataset_id: "ds-1", version_no: 3, iceberg_snapshot_id: 987654,
          schema: { claim_id: { type: "string", nullable: false } }, breaking_change: false,
          row_count: 100, bytes: 2048, profile_status: "completed", expired: false,
          created_at: "2026-07-12T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/datasets/ds-1/versions/3" && req.method === "GET") {
      return { status: 200, body: { data: { id: "dv-3", dataset_id: "ds-1", version_no: 3,
        schema: { claim_id: { type: "string" }, amount: { type: "double" } } } } };
    }
    if (req.path === "/api/v1/datasets:similar" && req.method === "POST") {
      return { status: 200, body: { data: [
        { id: "ds-2", urn: "wr:t-42:dataset:dataset/ds-2", name: "claims_2024", score: 0.91 },
        { id: "ds-1", urn: "wr:t-42:dataset:dataset/ds-1", name: "claims", score: 1.0 },
      ] } };
    }
    if (req.path === "/api/v1/datasets/ds-1/versions/3/profile" && req.method === "POST") {
      return { status: 202, body: { data: { operation_id: "prof-1", profile_id: "prof-1", status: "queued" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("dataset consumers/versions/similar/re-profile (dataset-service passthrough)", () => {
  it("datasetConsumers maps the depth-3 downstream rollup", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = datasetService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ datasetConsumers(id: "ds-1") { downstreamEdges byService byActivity truncated } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).datasetConsumers).toEqual({
      downstreamEdges: 4, byService: { query: 3, chart: 1 }, byActivity: { executed: 4 }, truncated: false,
    });
  });

  it("datasetVersions exposes the immutable version history", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = datasetService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ datasetVersions(datasetId: "ds-1", first: 10) {
          nodes { id versionNo icebergSnapshotId rowCount bytes profileStatus } pageInfo { hasMore } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).datasetVersions.nodes[0]).toMatchObject({
      id: "dv-3", versionNo: 3, icebergSnapshotId: "987654", rowCount: 100, bytes: 2048, profileStatus: "completed",
    });
  });

  it("similarDatasets seeds the search from the current version's schema and filters the dataset itself out", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = datasetService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ similarDatasets(datasetId: "ds-1") { id name score } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    // ds-1 (the seed itself) is filtered; only the true neighbor remains.
    expect((body?.data as any).similarDatasets).toEqual([{ id: "ds-2", name: "claims_2024", score: 0.91 }]);
    const post = requests.find((r) => r.path === "/api/v1/datasets:similar");
    expect(post?.body.columns).toEqual(["claim_id", "amount"]);
    expect(post?.body.schema).toMatchObject({ claim_id: { type: "string" } });
  });

  it("reprofileDataset defaults to the dataset's current version and returns the 202 ack", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = datasetService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { reprofileDataset(id: "ds-1") { operationId profileId status } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).reprofileDataset).toEqual({ operationId: "prof-1", profileId: "prof-1", status: "queued" });
    // versionNo omitted → resolver looked up the dataset and used version 3.
    expect(requests.some((r) => r.path === "/api/v1/datasets/ds-1/versions/3/profile" && r.method === "POST")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// semantic-service: verified queries (four-eyes) + bootstrap
// ---------------------------------------------------------------------------

const VQ = {
  id: "vq-1", workspace_id: "ws-9", model_id: null, nl_text: "top claims?",
  sql_text: "SELECT 1", variables: [], status: "draft", tags: ["hero"],
  provenance: null, health_note: null, submitted_by: "u-1", approved_by: null,
  decided_at: null, created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
};

function semanticVq(opts: { selfApprove?: boolean } = {}) {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/verified-queries" && req.method === "POST") {
      return { status: 201, body: { data: { ...VQ, workspace_id: req.body.workspace_id,
        nl_text: req.body.nl_text, sql_text: req.body.sql_text, tags: req.body.tags ?? [] } } };
    }
    if (req.path === "/api/v1/verified-queries" && req.method === "GET") {
      return { status: 200, body: { data: [{ ...VQ, status: "pending_review" }],
        page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/verified-queries:search" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "vq-1", nl_text: "top claims?", sql_text: "SELECT 1",
          variables: [], tags: ["hero"], model_id: null, score: 0.9312 }] } };
    }
    if (req.path === "/api/v1/verified-queries/vq-1" && req.method === "PATCH") {
      return { status: 200, body: { data: { ...VQ, nl_text: req.body.nl_text ?? VQ.nl_text } } };
    }
    if (req.path === "/api/v1/verified-queries/vq-1/submit") {
      return { status: 200, body: { data: { ...VQ, status: "pending_review" } } };
    }
    if (req.path === "/api/v1/verified-queries/vq-1/approve") {
      // Four-eyes double: semantic-service 403s when the caller authored the pair.
      if (opts.selfApprove) {
        return { status: 403, body: { error: { code: "PERMISSION_DENIED",
          message: "author cannot decide their own verified query (SEM-FR-040)", trace_id: "tr" } } };
      }
      return { status: 200, body: { data: { ...VQ, status: "approved", approved_by: "u-2",
        decided_at: "2026-07-12T02:00:00Z" } } };
    }
    if (req.path === "/api/v1/verified-queries/vq-1/reject") {
      return { status: 200, body: { data: { ...VQ, status: "rejected" } } };
    }
    if (req.path === "/api/v1/verified-queries/vq-1/archive") {
      return { status: 200, body: { data: { ...VQ, status: "archived" } } };
    }
    if (req.path === "/api/v1/models/sm-1/bootstrap" && req.method === "POST") {
      return { status: 202, body: { data: { operation_id: "op-1", status: "completed",
        report: { entities: 1, dimensions: 4, measures: 2 } } } };
    }
    if (req.path === "/api/v1/operations/op-1") {
      return { status: 200, body: { data: { operation_id: "op-1", kind: "bootstrap",
        status: "completed", report: { entities: 1 }, created_at: "2026-07-12T00:00:00Z",
        finished_at: "2026-07-12T00:00:05Z" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("verified NL↔SQL pairs + bootstrap (semantic-service passthrough, four-eyes downstream)", () => {
  it("createVerifiedQuery threads the JWT workspace and fails closed without one", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semanticVq();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-9" });
    const res = await server.executeOperation(
      { query: `mutation($input: CreateVerifiedQueryInput!) {
          createVerifiedQuery(input: $input) { id status nlText workspaceId urn }
        }`,
        variables: { input: { nlText: "top claims?", sqlText: "SELECT 1", tags: ["hero"] } } },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createVerifiedQuery).toMatchObject({
      id: "vq-1", status: "DRAFT", workspaceId: "ws-9", urn: "wr:t-42:semantic:verified_query/vq-1",
    });
    expect(requests.find((r) => r.method === "POST")?.body.workspace_id).toBe("ws-9");

    const noWs = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"] });
    const res2 = await server.executeOperation(
      { query: `mutation { createVerifiedQuery(input: { nlText: "x", sqlText: "SELECT 1" }) { id } }` },
      { contextValue: noWs },
    );
    expect(single(res2)?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
  });

  it("verifiedQueries lowercases the status filter for the service's exact-match filter", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semanticVq();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ verifiedQueries(status: "PENDING_REVIEW", first: 10) { nodes { id status } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).verifiedQueries.nodes[0].status).toBe("PENDING_REVIEW");
    expect(requests[0]?.search.get("filter[status]")).toBe("pending_review");
  });

  it("verifiedQuerySearch forwards q/workspace/top_k and maps the ANN hits", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semanticVq();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ verifiedQuerySearch(query: "top claims", workspaceId: "ws-9", topK: 3) {
          id nlText sqlText tags modelId score } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    const hits = (body?.data as any).verifiedQuerySearch;
    expect(hits).toEqual([
      { id: "vq-1", nlText: "top claims?", sqlText: "SELECT 1", tags: ["hero"],
        modelId: null, score: 0.9312 },
    ]);
    const searchReq = requests.find((r) => r.path === "/api/v1/verified-queries:search");
    expect(searchReq?.search.get("q")).toBe("top claims");
    expect(searchReq?.search.get("workspace_id")).toBe("ws-9");
    expect(searchReq?.search.get("top_k")).toBe("3");
  });

  it("submit/approve/reject/archive drive the real lifecycle routes", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semanticVq();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-2", tenant_id: "t-42", typ: "user", scopes: ["*"] });
    const res = await server.executeOperation(
      { query: `mutation {
          submitVerifiedQuery(id: "vq-1") { status }
          approveVerifiedQuery(id: "vq-1") { status approvedBy }
          rejectVerifiedQuery(id: "vq-1", note: "needs work") { status }
          archiveVerifiedQuery(id: "vq-1") { status }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect(body?.data).toMatchObject({
      submitVerifiedQuery: { status: "PENDING_REVIEW" },
      approveVerifiedQuery: { status: "APPROVED", approvedBy: "u-2" },
      rejectVerifiedQuery: { status: "REJECTED" },
      archiveVerifiedQuery: { status: "ARCHIVED" },
    });
    expect(requests.find((r) => r.path.endsWith("/reject"))?.body).toEqual({ note: "needs work" });
  });

  it("approveVerifiedQuery surfaces the four-eyes 403 as PERMISSION_DENIED untouched", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticVq({ selfApprove: true });
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"] });
    const res = await server.executeOperation(
      { query: `mutation { approveVerifiedQuery(id: "vq-1") { id } }` },
      { contextValue: ctx },
    );
    expect(single(res)?.errors?.[0]?.extensions?.code).toBe("PERMISSION_DENIED");
  });

  it("bootstrapSemanticModel returns the 202 operation; semanticOperation polls it", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticVq();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { bootstrapSemanticModel(modelId: "sm-1") { operationId status report } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).bootstrapSemanticModel).toMatchObject({ operationId: "op-1", status: "completed" });

    const poll = await server.executeOperation(
      { query: `{ semanticOperation(id: "op-1") { operationId kind status finishedAt } }` },
      { contextValue: ctx },
    );
    expect((single(poll)?.data as any).semanticOperation).toMatchObject({
      operationId: "op-1", kind: "bootstrap", status: "completed",
    });
  });
});

// ---------------------------------------------------------------------------
// pipeline-orchestrator: run + template lifecycle
// ---------------------------------------------------------------------------

const TEMPLATE = {
  id: "tpl-1", workspace_id: "ws-9", name: "training", pipeline_type: "training",
  active_version_id: "tv-2", is_system: false, archived: false,
  created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
};

function pipelineService() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/runs/run-1/terminate" && req.method === "PUT") {
      return { status: 200, body: { data: { id: "run-1", template_id: "tpl-1", status: "cancelled",
        finished_at: "2026-07-12T01:00:00Z" } } };
    }
    if (req.path === "/api/v1/runs/run-1/retry" && req.method === "POST") {
      return { status: 202, body: { operation_id: "op-r", data: { id: "run-2", template_id: "tpl-1",
        status: "submitted", retried_from_run_id: "run-1" } } };
    }
    if (req.path === "/api/v1/runs/run-1/manifest" && req.method === "GET") {
      return { status: 200, body: { data: { run_id: "run-1",
        manifest: { kind: "Workflow", spec: { entrypoint: "main" } },
        resolved_parameters: { epochs: 5 } } } };
    }
    if (req.path === "/api/v1/pipelines/tpl-1/versions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "tv-2", template_id: "tpl-1", version_no: 2, validation_status: "valid",
          manifest_digest: "sha256:abc", argo_template_name: "wf-tpl-1", created_at: "2026-07-12T00:00:00Z" },
        { id: "tv-1", template_id: "tpl-1", version_no: 1, validation_status: "draft",
          created_at: "2026-07-10T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/pipelines/tpl-1/versions/tv-1/activate" && req.method === "POST") {
      return { status: 200, body: { data: { ...TEMPLATE, active_version_id: "tv-1" } } };
    }
    if (req.path === "/api/v1/pipelines/tpl-1/clone" && req.method === "POST") {
      return { status: 201, body: { data: { ...TEMPLATE, id: "tpl-2", name: "training (copy)" } } };
    }
    if (req.path === "/api/v1/pipelines/tpl-1/compile" && req.method === "POST") {
      return { status: 200, body: { data: { template_id: "tpl-1", version_id: "tv-2",
        manifest_digest: "sha256:abc", argo_template_name: "wf-tpl-1",
        manifest: { kind: "WorkflowTemplate" } } } };
    }
    if (req.path === "/api/v1/pipelines/tpl-1" && req.method === "DELETE") {
      return { status: 200, body: { data: { ...TEMPLATE, archived: true } } };
    }
    if (req.path === "/api/v1/pipelines/sys-1" && req.method === "DELETE") {
      return { status: 409, body: { error: { code: "CONFLICT",
        message: "system-owned templates cannot be archived", trace_id: "tr" } } };
    }
    if (req.path === "/api/v1/pipelines/tpl-1/restore" && req.method === "PATCH") {
      return { status: 200, body: { data: { ...TEMPLATE, archived: false } } };
    }
    if (req.path === "/api/v1/pipelines" && req.method === "GET") {
      return { status: 200, body: { data: [{ ...TEMPLATE, archived: true }],
        page: { next_cursor: null, has_more: false } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("pipeline run + template lifecycle (pipeline-orchestrator passthrough)", () => {
  it("terminatePipelineRun PUTs /terminate; retryPipelineRun POSTs /retry and returns the NEW run", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = pipelineService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          terminatePipelineRun(id: "run-1") { id status }
          retryPipelineRun(id: "run-1") { id status retriedFromRunId }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect(body?.data).toMatchObject({
      terminatePipelineRun: { id: "run-1", status: "cancelled" },
      retryPipelineRun: { id: "run-2", status: "submitted", retriedFromRunId: "run-1" },
    });
    expect(requests.some((r) => r.path === "/api/v1/runs/run-1/terminate" && r.method === "PUT")).toBe(true);
  });

  it("pipelineRunManifest returns the compiled manifest + resolved parameters", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipelineService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ pipelineRunManifest(id: "run-1") { runId manifest resolvedParameters } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).pipelineRunManifest).toEqual({
      runId: "run-1", manifest: { kind: "Workflow", spec: { entrypoint: "main" } },
      resolvedParameters: { epochs: 5 },
    });
  });

  it("pipelineTemplateVersions lists versions; activate flips the active version", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipelineService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ pipelineTemplateVersions(templateId: "tpl-1", first: 10) {
          nodes { id versionNo validationStatus manifestDigest } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).pipelineTemplateVersions.nodes).toHaveLength(2);

    const act = await server.executeOperation(
      { query: `mutation { activatePipelineTemplateVersion(templateId: "tpl-1", versionId: "tv-1") { activeVersionId } }` },
      { contextValue: ctx },
    );
    expect((single(act)?.data as any).activatePipelineTemplateVersion.activeVersionId).toBe("tv-1");
  });

  it("clone/compile/delete/restore drive the template lifecycle; system-template delete 409s as CONFLICT", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipelineService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation {
          clonePipelineTemplate(id: "tpl-1") { id name }
          compilePipelineTemplate(id: "tpl-1") { manifestDigest argoTemplateName manifest }
          deletePipelineTemplate(id: "tpl-1") { id archived }
          restorePipelineTemplate(id: "tpl-1") { id archived }
        }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect(body?.data).toMatchObject({
      clonePipelineTemplate: { id: "tpl-2", name: "training (copy)" },
      compilePipelineTemplate: { manifestDigest: "sha256:abc", argoTemplateName: "wf-tpl-1" },
      deletePipelineTemplate: { id: "tpl-1", archived: true },
      restorePipelineTemplate: { id: "tpl-1", archived: false },
    });

    const sys = await server.executeOperation(
      { query: `mutation { deletePipelineTemplate(id: "sys-1") { id } }` },
      { contextValue: ctx },
    );
    expect(single(sys)?.errors?.[0]?.extensions?.code).toBe("CONFLICT");
  });

  it("pipelineTemplates(includeArchived: true) forwards include_archived and maps the archived flag", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = pipelineService();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ pipelineTemplates(first: 10, includeArchived: true) { nodes { id archived isSystem } } }` },
      { contextValue: ctx },
    );
    const body = single(res);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).pipelineTemplates.nodes[0]).toMatchObject({ id: "tpl-1", archived: true, isSystem: false });
    expect(requests[0]?.search.get("include_archived")).toBe("true");
  });
});

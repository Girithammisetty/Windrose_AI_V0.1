import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** Downstream double for query-service + ingestion-service + dataset lineage. */
function downstream() {
  return mockFetch((req: CapturedRequest) => {
    // --- query-service ------------------------------------------------------
    if (req.path === "/api/v1/queries" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "q-1", name: "Open claims by type", tags: ["claims"], module_names: ["insights"], current_version_no: 3, updated_at: "2026-07-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/sql/run" && req.method === "POST") {
      // sync run returns a terminal execution.
      return {
        status: 200,
        body: { data: { execution_id: "ex-1", id: "ex-1", status: "succeeded", engine: "duckdb", cache_hit: false, stats: { result_rows: 1, duration_ms: 12 } } },
      };
    }
    if (req.path === "/api/v1/executions/ex-1/results" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: {
            columns: [{ name: "n", type: "BIGINT" }],
            rows: [[1]],
            page: { has_more: false },
            stats: { result_rows: 1, duration_ms: 12, engine: "duckdb", cache_hit: false },
          },
        },
      };
    }
    if (req.path === "/api/v1/queries/q-1/run" && req.method === "POST") {
      return { status: 200, body: { data: { execution_id: "ex-2", status: "succeeded", engine: "duckdb", stats: { result_rows: 0 } } } };
    }
    if (req.path === "/api/v1/executions/ex-2/results" && req.method === "GET") {
      return { status: 200, body: { data: { columns: [{ name: "claim_type" }], rows: [], page: { has_more: false } } } };
    }
    // --- ingestion-service --------------------------------------------------
    if (req.path === "/api/v1/ingestions" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "ing-1", ingestion_mode: "query", status: "succeeded", dataset_urn: "wr:t-42:dataset:dataset/ds-9", rows_appended: 100, created_at: "2026-07-01T00:00:00Z" },
          ],
          page: { has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/ingestions" && req.method === "POST") {
      return { status: 201, body: { data: { id: "ing-2", ingestion_mode: "query", status: "created", dataset_urn: "wr:t-42:dataset:dataset/ds-new" } } };
    }
    // --- ingestion-service: resumable uploads (session lifecycle only) ------
    if (req.path === "/api/v1/uploads" && req.method === "POST") {
      return {
        status: 201,
        body: { data: { upload_id: "up-1", ingestion_id: req.body.ingestion_id, status: "created",
          part_size: req.body.part_size ?? 8_000_000, bytes_total: req.body.bytes_total ?? null,
          expires_at: "2026-07-12T01:00:00Z", parts: [] } },
      };
    }
    if (req.path === "/api/v1/uploads/up-1" && req.method === "GET") {
      return {
        status: 200,
        body: { data: { upload_id: "up-1", ingestion_id: "ing-up", status: "created", part_size: 8_000_000,
          bytes_total: 16_000_000, sha256: null, expires_at: "2026-07-12T01:00:00Z",
          parts: [{ n: 1, etag: "etag-1", size: 8_000_000 }] } },
      };
    }
    if (req.path === "/api/v1/uploads/up-1/complete" && req.method === "POST") {
      return {
        status: 202,
        body: { data: { id: "ing-up", ingestion_mode: "file_upload", status: "queued",
          bytes_total: 16_000_000, bytes_received: 16_000_000, created_at: "2026-07-12T00:00:00Z" } },
      };
    }
    // --- dataset-service lineage --------------------------------------------
    if (req.path === "/api/v1/lineage" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: {
            nodes: [{ urn: "wr:t-42:dataset:dataset/ds-9", kind: "dataset" }, { urn: "wr:t-42:ingestion:ingestion/ing-1", kind: "foreign" }],
            edges: [{ from_urn: "wr:t-42:ingestion:ingestion/ing-1", to_urn: "wr:t-42:dataset:dataset/ds-9", activity: "ingest", occurred_at: "2026-07-01T00:00:00Z" }],
            truncated: false,
          },
        },
      };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("data-plane resolvers (queries, ingestions, lineage)", () => {
  it("runSql executes sync then hydrates the first results page (columns + rows)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      { query: `mutation { runSql(input:{ sql:"SELECT 1 AS n" }) { executionId status engine resultRows columns { name type } rows } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const r: any = body?.data?.runSql;
    expect(r.status).toBe("succeeded");
    expect(r.engine).toBe("duckdb");
    expect(r.columns).toEqual([{ name: "n", type: "BIGINT" }]);
    expect(r.rows).toEqual([[1]]);
    // Two real calls: POST /sql/run then GET the results.
    expect(requests.some((q) => q.path === "/api/v1/sql/run" && q.method === "POST")).toBe(true);
    expect(requests.some((q) => q.path === "/api/v1/executions/ex-1/results")).toBe(true);
    // JWT forwarded verbatim (no BFF authz decision).
    expect(requests[0]?.headers.authorization).toMatch(/^Bearer /);
  });

  it("savedQueries lists + runSavedQuery runs a saved query by id", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const list = await server.executeOperation(
      { query: `{ savedQueries { nodes { id name urn versionNo } pageInfo { hasMore } } }` },
      { contextValue: ctx },
    );
    const lb = list.body.kind === "single" ? list.body.singleResult : null;
    expect(lb?.errors).toBeUndefined();
    expect(lb?.data?.savedQueries).toMatchObject({
      nodes: [{ id: "q-1", name: "Open claims by type", urn: "wr:t-42:query:query/q-1", versionNo: 3 }],
    });

    const run = await server.executeOperation(
      { query: `mutation { runSavedQuery(id:"q-1") { executionId status columns { name } rows } }` },
      { contextValue: ctx },
    );
    const rb = run.body.kind === "single" ? run.body.singleResult : null;
    expect(rb?.errors).toBeUndefined();
    expect((rb?.data?.runSavedQuery as any).status).toBe("succeeded");
  });

  it("ingestions lists runs and createIngestion lands a new dataset", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const list = await server.executeOperation(
      { query: `{ ingestions { nodes { id mode status datasetUrn rowsAppended urn } } }` },
      { contextValue: ctx },
    );
    const lb = list.body.kind === "single" ? list.body.singleResult : null;
    expect(lb?.errors).toBeUndefined();
    expect((lb?.data?.ingestions as any).nodes[0]).toMatchObject({
      id: "ing-1", mode: "query", status: "succeeded", rowsAppended: 100, urn: "wr:t-42:ingestion:ingestion/ing-1",
    });

    const create = await server.executeOperation(
      { query: `mutation { createIngestion(input:{ mode:"query", connectionId:"c-1", statement:"SELECT 1", newDatasetName:"fresh" }) { id status } }` },
      { contextValue: ctx },
    );
    const cb = create.body.kind === "single" ? create.body.singleResult : null;
    expect(cb?.errors).toBeUndefined();
    expect((cb?.data?.createIngestion as any).id).toBe("ing-2");
    const post = requests.find((q) => q.path === "/api/v1/ingestions" && q.method === "POST");
    expect(post?.body).toMatchObject({ ingestion_mode: "query", connection_id: "c-1", new_dataset: { name: "fresh" } });
  });

  it("createUpload creates a session, upload reads status/parts, completeUpload finalizes into the Ingestion", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const create = await server.executeOperation(
      {
        query: `mutation($input: CreateUploadInput!) { createUpload(input: $input) { uploadId ingestionId status partSize bytesTotal expiresAt } }`,
        variables: { input: { ingestionId: "ing-up", partSize: 8000000, bytesTotal: 16000000 } },
      },
      { contextValue: ctx },
    );
    const cb = create.body.kind === "single" ? create.body.singleResult : null;
    expect(cb?.errors).toBeUndefined();
    expect(cb?.data?.createUpload).toMatchObject({
      uploadId: "up-1", ingestionId: "ing-up", status: "created", partSize: 8000000, bytesTotal: 16000000,
    });
    const post = requests.find((q) => q.path === "/api/v1/uploads" && q.method === "POST");
    expect(post?.body).toMatchObject({ ingestion_id: "ing-up", part_size: 8000000, bytes_total: 16000000 });

    const status = await server.executeOperation(
      { query: `{ upload(id: "up-1") { uploadId status parts { n etag size } } }` },
      { contextValue: ctx },
    );
    const sb = status.body.kind === "single" ? status.body.singleResult : null;
    expect(sb?.errors).toBeUndefined();
    expect((sb?.data?.upload as any).parts).toEqual([{ n: 1, etag: "etag-1", size: 8_000_000 }]);

    const complete = await server.executeOperation(
      {
        query: `mutation($uploadId: ID!, $input: CompleteUploadInput!) { completeUpload(uploadId: $uploadId, input: $input) { id status bytesReceived } }`,
        variables: { uploadId: "up-1", input: { parts: [{ n: 1, etag: "etag-1", size: 8_000_000 }, { n: 2, etag: "etag-2", size: 8_000_000 }] } },
      },
      { contextValue: ctx },
    );
    const comb = complete.body.kind === "single" ? complete.body.singleResult : null;
    expect(comb?.errors).toBeUndefined();
    // completeUpload returns the serialized Ingestion (queued), not an Upload.
    expect(comb?.data?.completeUpload).toMatchObject({ id: "ing-up", status: "queued", bytesReceived: 16_000_000 });
    const completePost = requests.find((q) => q.path === "/api/v1/uploads/up-1/complete" && q.method === "POST");
    expect(completePost?.body).toMatchObject({
      parts: [{ n: 1, etag: "etag-1", size: 8_000_000 }, { n: 2, etag: "etag-2", size: 8_000_000 }],
    });
  });

  it("datasetLineage maps the URN graph (nodes + edges)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = downstream();
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      { query: `{ datasetLineage(urn:"wr:t-42:dataset:dataset/ds-9") { nodes { urn kind } edges { fromUrn toUrn activity } truncated } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const g: any = body?.data?.datasetLineage;
    expect(g.nodes).toHaveLength(2);
    expect(g.edges[0]).toMatchObject({ fromUrn: "wr:t-42:ingestion:ingestion/ing-1", toUrn: "wr:t-42:dataset:dataset/ds-9", activity: "ingest" });
    expect(g.truncated).toBe(false);
  });
});

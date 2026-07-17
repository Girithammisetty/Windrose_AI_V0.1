import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

const DEFINITION = {
  entities: [
    { name: "claims", dataset_urn: "wr:t-42:dataset:dataset/d1", table: "main.claims",
      primary_key: ["claim_id"], dataset_version_policy: { policy: "latest" } },
  ],
  dimensions: [
    { name: "claim_type", entity: "claims", column: "claim_type", type: "categorical",
      time_grains: [], synonyms: [], deprecated: false },
  ],
  measures: [
    { name: "claim_count", entity: "claims", agg: "count", synonyms: [], deprecated: false },
  ],
  join_paths: [],
};

/** semantic-service double covering the authoring surface: create model, list/get
 * versions, patch-draft (200 + 422 structural), submit (200 + 422 full-validation
 * list), approve (200 + 403 self-approve), reject (200 + 422 missing note). */
function semanticAuthoring() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/models" && req.method === "POST") {
      return {
        status: 201,
        body: {
          data: {
            id: "sm-new", workspace_id: req.body.workspace_id, name: req.body.name,
            description: req.body.description ?? null, published_version_id: null,
            published_version_no: null, health: { status: "ok", broken_refs: [] },
            created_by: "u-1", created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
            draft_version: { id: "ver-new", model_id: "sm-new", version_no: 1, status: "draft",
                            created_at: "2026-07-12T00:00:00Z" },
          },
        },
      };
    }
    if (req.path === "/api/v1/models" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "sm-1", workspace_id: "ws-9", name: "claims_core", published_version_no: 2,
              health: { status: "ok", broken_refs: [] }, created_by: "u-1",
              created_at: "2026-07-11T00:00:00Z", updated_at: "2026-07-11T00:00:00Z" },
          ],
          page: { next_cursor: "c2", has_more: true },
        },
      };
    }
    if (req.path === "/api/v1/models/sm-1" && req.method === "PATCH") {
      return { status: 200, body: { data: { id: "sm-1", workspace_id: "ws-9", name: req.body.name ?? "claims_core",
                description: req.body.description ?? null, created_at: "2026-07-11T00:00:00Z",
                updated_at: "2026-07-12T00:00:00Z" } } };
    }
    if (req.path === "/api/v1/models/sm-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    if (req.path === "/api/v1/models/sm-1/versions" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "ver-2", model_id: "sm-1", version_no: 2, status: "draft", created_at: "2026-07-12T00:00:00Z" },
            { id: "ver-1", model_id: "sm-1", version_no: 1, status: "superseded", created_at: "2026-07-10T00:00:00Z" },
          ],
          page: { next_cursor: null, has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/models/sm-1/versions/2" && req.method === "GET") {
      return { status: 200, body: { data: { id: "ver-2", model_id: "sm-1", version_no: 2, status: "draft",
                definition: DEFINITION, submitted_by: null, approved_by: null,
                created_at: "2026-07-12T00:00:00Z" } } };
    }
    // patch draft: a bad expr in the body -> 422 structural (single message, no details list)
    if (req.path === "/api/v1/models/sm-1/versions/2" && req.method === "PATCH") {
      if (req.body.definition?.__bad_expr) {
        return { status: 422, body: { error: { code: "EXPRESSION_NOT_ALLOWED",
                  message: "illegal column identifier 'SELECT'", trace_id: "tr-1" } } };
      }
      return { status: 200, body: { data: { id: "ver-2", model_id: "sm-1", version_no: 2, status: "draft",
                definition: req.body.definition, created_at: "2026-07-12T00:00:00Z" } } };
    }
    // submit: /versions/3 -> full validation failure (object/problem list)
    if (req.path === "/api/v1/models/sm-1/versions/3/submit" && req.method === "POST") {
      return { status: 422, body: { error: { code: "VALIDATION_FAILED", message: "definition validation failed",
                trace_id: "tr-2", details: [
                  { object: "dimension/bogus_dim", problem: "column 'nope' not in dataset schema of entity 'claims'" },
                ] } } };
    }
    // submit: /versions/2 -> succeeds, moves to in_review
    if (req.path === "/api/v1/models/sm-1/versions/2/submit" && req.method === "POST") {
      return { status: 200, body: { data: { id: "ver-2", model_id: "sm-1", version_no: 2, status: "in_review",
                submitted_by: "u-1", created_at: "2026-07-12T00:00:00Z" } } };
    }
    // approve: self-approve -> 403
    if (req.path === "/api/v1/models/sm-1/versions/2/approve" && req.method === "POST") {
      return { status: 403, body: { error: { code: "PERMISSION_DENIED",
                message: "author cannot approve their own version (SEM-FR-007)", trace_id: "tr-3" } } };
    }
    // reject: missing note -> 422; with note -> 200
    if (req.path === "/api/v1/models/sm-1/versions/2/reject" && req.method === "POST") {
      if (!req.body.note) {
        return { status: 422, body: { error: { code: "VALIDATION_FAILED",
                  message: "a decision note is required to reject", trace_id: "tr-4" } } };
      }
      return { status: 200, body: { data: { id: "ver-2", model_id: "sm-1", version_no: 2, status: "rejected",
                decision_note: req.body.note, created_at: "2026-07-12T00:00:00Z" } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("semantic model authoring resolvers (semantic-service passthrough, JWT forwarded)", () => {
  it("createSemanticModel sources workspace_id from the JWT claim and returns the opened draft's version number", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-9" });
    const res = await server.executeOperation(
      {
        query: `mutation($input: CreateSemanticModelInput!) {
          createSemanticModel(input: $input) { id name workspaceId draftVersionNo urn }
        }`,
        variables: { input: { name: "claims_core", description: "d" } },
      },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const m = (body?.data as any).createSemanticModel;
    expect(m).toMatchObject({ id: "sm-new", name: "claims_core", workspaceId: "ws-9", draftVersionNo: 1 });
    expect(m.urn).toBe("wr:t-42:semantic:model/sm-new");
    const post = requests.find((r) => r.path === "/api/v1/models" && r.method === "POST");
    expect(post?.body.workspace_id).toBe("ws-9");
  });

  it("createSemanticModel fails closed with VALIDATION_FAILED when the caller token has no workspace", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"] });
    const res = await server.executeOperation(
      { query: `mutation($input: CreateSemanticModelInput!) { createSemanticModel(input: $input) { id } }`,
        variables: { input: { name: "x" } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
  });

  it("semanticModelList cursor-paginates model summaries", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ semanticModelList(workspaceId: "ws-9", first: 10) {
          nodes { id name publishedVersionNo healthStatus } pageInfo { nextCursor hasMore } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).semanticModelList;
    expect(conn.nodes[0]).toMatchObject({ id: "sm-1", name: "claims_core", publishedVersionNo: 2, healthStatus: "ok" });
    expect(conn.pageInfo).toEqual({ nextCursor: "c2", hasMore: true });
  });

  it("semanticModelVersion maps the raw JSON definition to typed entities/dimensions/measures", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ semanticModelVersion(modelId: "sm-1", versionNo: 2) {
          versionNo status
          definition {
            entities { name datasetUrn table primaryKey }
            dimensions { name entity column dimType }
            measures { name entity agg }
          }
        } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const v = (body?.data as any).semanticModelVersion;
    expect(v.status).toBe("DRAFT");
    expect(v.definition.entities[0]).toMatchObject({ name: "claims", table: "main.claims", primaryKey: ["claim_id"] });
    expect(v.definition.dimensions[0]).toMatchObject({ name: "claim_type", column: "claim_type", dimType: "categorical" });
    expect(v.definition.measures[0]).toMatchObject({ name: "claim_count", agg: "count" });
  });

  it("updateSemanticModelDraft (save) surfaces a real structural 422 (bad expr) as VALIDATION_FAILED-shaped error", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($d: JSON!) { updateSemanticModelDraft(modelId: "sm-1", versionNo: 2, definition: $d) { versionNo } }`,
        variables: { d: { __bad_expr: true } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
    expect(body?.errors?.[0]?.message).toMatch(/illegal column identifier/);
  });

  it("updateSemanticModelDraft (save) succeeds and returns the saved definition", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($d: JSON!) { updateSemanticModelDraft(modelId: "sm-1", versionNo: 2, definition: $d) { status definitionJson } }`,
        variables: { d: DEFINITION } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateSemanticModelDraft.status).toBe("DRAFT");
    const patch = requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/models/sm-1/versions/2");
    expect(patch?.body.definition).toEqual(DEFINITION);
  });

  it("submitSemanticModelVersion surfaces the full [{object,problem}] validation list in error details", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { submitSemanticModelVersion(modelId: "sm-1", versionNo: 3) { status } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");
    const details = body?.errors?.[0]?.extensions?.details as any[];
    expect(details).toEqual([
      { object: "dimension/bogus_dim", problem: "column 'nope' not in dataset schema of entity 'claims'" },
    ]);
  });

  it("submitSemanticModelVersion succeeds and transitions to IN_REVIEW", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { submitSemanticModelVersion(modelId: "sm-1", versionNo: 2) { status submittedBy } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).submitSemanticModelVersion).toEqual({ status: "IN_REVIEW", submittedBy: "u-1" });
  });

  it("approveSemanticModelVersion surfaces the real 403 when the caller authored the version (four-eyes)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { approveSemanticModelVersion(modelId: "sm-1", versionNo: 2) { status } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe("PERMISSION_DENIED");
    expect(body?.errors?.[0]?.message).toMatch(/author cannot approve/);
  });

  it("rejectSemanticModelVersion requires a note (422 without one; succeeds with one)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);

    const missing = await server.executeOperation(
      { query: `mutation { rejectSemanticModelVersion(modelId: "sm-1", versionNo: 2, note: "") { status } }` },
      { contextValue: ctx },
    );
    const missingBody = missing.body.kind === "single" ? missing.body.singleResult : null;
    expect(missingBody?.errors?.[0]?.extensions?.code).toBe("VALIDATION_FAILED");

    const withNote = await server.executeOperation(
      { query: `mutation { rejectSemanticModelVersion(modelId: "sm-1", versionNo: 2, note: "missing filters") { status decisionNote } }` },
      { contextValue: ctx },
    );
    const withNoteBody = withNote.body.kind === "single" ? withNote.body.singleResult : null;
    expect(withNoteBody?.errors).toBeUndefined();
    expect((withNoteBody?.data as any).rejectSemanticModelVersion).toEqual({ status: "REJECTED", decisionNote: "missing filters" });
  });

  it("deleteSemanticModel and updateSemanticModel reshape the header", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = semanticAuthoring();
    const ctx = await makeTestContext(fetchImpl);

    const upd = await server.executeOperation(
      { query: `mutation { updateSemanticModel(id: "sm-1", input: { description: "new desc" }) { name description } }` },
      { contextValue: ctx },
    );
    const updBody = upd.body.kind === "single" ? upd.body.singleResult : null;
    expect(updBody?.errors).toBeUndefined();
    expect((updBody?.data as any).updateSemanticModel).toEqual({ name: "claims_core", description: "new desc" });

    const del = await server.executeOperation(
      { query: `mutation { deleteSemanticModel(id: "sm-1") }` },
      { contextValue: ctx },
    );
    const delBody = del.body.kind === "single" ? del.body.singleResult : null;
    expect(delBody?.errors).toBeUndefined();
    expect((delBody?.data as any).deleteSemanticModel).toBe(true);
  });
});

/** dataset-service double: a version with a populated schema (d1) and one with an
 * empty schema but a completed profile (d2) — exercises the datasetSchema
 * fallback-to-profile path (a real, documented data-quality gap, not faked). */
function datasetSchemaDouble() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/datasets/d1/versions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "v1", dataset_id: "d1", version_no: 1,
          schema: { claim_type: { type: "string", nullable: false, tags: [] }, amount: { type: "double", nullable: true, tags: ["pii"] } },
          created_at: "2026-07-12T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/datasets/d2/versions" && req.method === "GET") {
      return { status: 200, body: { data: [
        { id: "v2", dataset_id: "d2", version_no: 1, schema: {}, created_at: "2026-07-12T00:00:00Z" },
      ], page: { next_cursor: null, has_more: false } } };
    }
    if (req.path === "/api/v1/datasets/d2/profile" && req.method === "GET") {
      return { status: 200, body: { data: {
        status: "completed", columns: [
          { name: "claim_type", logical_type: "string", null_pct: 0, distinct_count: 3 },
          { name: "amount", logical_type: "double", null_pct: 0, distinct_count: 12 },
        ],
      } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("datasetSchema resolver (dataset-service passthrough)", () => {
  it("returns the version's authoritative schema map when populated", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = datasetSchemaDouble();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ datasetSchema(datasetId: "d1") { name type nullable tags inferred } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const cols = (body?.data as any).datasetSchema;
    expect(cols).toEqual([
      { name: "claim_type", type: "string", nullable: false, tags: [], inferred: false },
      { name: "amount", type: "double", nullable: true, tags: ["pii"], inferred: false },
    ]);
  });

  it("falls back to the profile's columns (inferred: true) when the version schema is empty", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = datasetSchemaDouble();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ datasetSchema(datasetId: "d2") { name type inferred } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const cols = (body?.data as any).datasetSchema;
    expect(cols).toEqual([
      { name: "claim_type", type: "string", inferred: true },
      { name: "amount", type: "double", inferred: true },
    ]);
  });
});

/** compile double: SQL compiles fine; ?validate=true 500s (mirrors the real
 * semantic-service<->query-service dry-run integration gap observed live). */
function compileDouble() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/compile" && req.search.get("validate") === "true") {
      return { status: 500, body: { error: { code: "INTERNAL", message: "internal server error", trace_id: "t" } } };
    }
    if (req.path === "/api/v1/compile") {
      return { status: 200, body: { data: {
        sql: "SELECT \"c\".\"claim_type\" AS \"claim_type\", count(*) AS \"claim_count\" FROM \"main\".\"claims\" \"c\" GROUP BY 1",
        engine_dialect: "trino",
        output_schema: [{ name: "claim_type", type: "string", role: "dimension" }, { name: "claim_count", type: "bigint", role: "measure" }],
        provenance: { model_version: "claims_core@v2" },
        warnings: [],
      } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("compileSemanticModel resolver (semantic-service passthrough)", () => {
  it("returns the compiled SQL + schema and forwards X-Draft-Version for a draft preview", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = compileDouble();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query($input: CompileSemanticModelInput!) { compileSemanticModel(input: $input) {
          sql outputSchema { name role } validationAvailable
        } }`,
        variables: { input: { model: "sm-1", metrics: ["claim_count"], dimensions: [{ name: "claim_type" }], draftVersionNo: 2 } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const r = (body?.data as any).compileSemanticModel;
    expect(r.sql).toMatch(/SELECT/);
    expect(r.outputSchema).toEqual([{ name: "claim_type", role: "dimension" }, { name: "claim_count", role: "measure" }]);
    expect(r.validationAvailable).toBe(false);
    const compile = requests.find((req) => req.path === "/api/v1/compile" && !req.search.has("validate"));
    expect(compile?.headers["x-draft-version"]).toBe("2");
  });

  it("degrades gracefully when validate:true's dry-run integration fails: still returns real SQL, marks validation unavailable", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = compileDouble();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `query($input: CompileSemanticModelInput!) { compileSemanticModel(input: $input) {
          sql validationAvailable validationMessage
        } }`,
        variables: { input: { model: "sm-1", metrics: ["claim_count"], validate: true } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const r = (body?.data as any).compileSemanticModel;
    expect(r.sql).toMatch(/SELECT/);
    expect(r.validationAvailable).toBe(false);
    expect(r.validationMessage).toBeTruthy();
    // Two real calls: the failed validate attempt, then the plain retry.
    expect(requests.filter((req) => req.path === "/api/v1/compile").length).toBe(2);
  });
});

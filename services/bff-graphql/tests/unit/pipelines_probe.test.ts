import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** Reproduce the orchestrator's REAL two-flavours-of-422 on POST /pipelines/validate:
 *  - report-invalid  -> 422 { data: { status:"draft", items:[...] } }
 *  - request-invalid -> 422 { error: { code:"VALIDATION_FAILED", ... } }  (pydantic)
 * (see services/pipeline-orchestrator/app/api/middleware.py validation_handler) */
function orch() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/pipelines/validate" && req.method === "POST") {
      // Emulate FastAPI RequestValidationError -> master error envelope @ 422.
      if (req.body?.pipeline_type === "bogus_type") {
        return {
          status: 422,
          body: {
            error: {
              code: "VALIDATION_FAILED",
              message: "request validation failed",
              trace_id: "t",
              details: [{ field: "body.pipeline_type", problem: "Input should be 'training' ..." }],
            },
          },
        };
      }
      // Normal report-invalid outcome.
      return { status: 422, body: { data: { status: "draft", items: [{ code: "EMPTY_DAG", alias: null, field: null, problem: "no nodes" }] } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("PROBE: validate() must not swallow a pydantic 422 as an empty invalid report", () => {
  it("a request-validation 422 (bad pipeline_type) should surface VALIDATION_FAILED, not {valid:false, issues:[]}", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = orch();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($d: JSON!) { validatePipeline(definition: $d, pipelineType: "bogus_type") { valid issues { code message node } } }`,
        variables: { d: { nodes: [{ alias: "a" }], edges: [] } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    // A malformed request must NOT be silently reported as an empty invalid report.
    const data = body?.data as any;
    const swallowed =
      body?.errors === undefined &&
      data?.validatePipeline &&
      data.validatePipeline.valid === false &&
      data.validatePipeline.issues.length === 0;
    expect(swallowed, "pydantic 422 was swallowed into {valid:false, issues:[]}").toBe(false);
  });
});

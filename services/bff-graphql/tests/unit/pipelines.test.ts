import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** pipeline-orchestrator double: component/algorithm catalog + template CRUD +
 * validate (200 valid / 422 invalid) + run submission (202). */
function pipeline() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/components" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: {
            catalog_version: "windrose-catalog/1.0.0",
            groups: {
              io: [
                {
                  name: "read-from-warehouse", component_type: "io", label: "Read From Warehouse",
                  enabled: true, min_inputs: 0, max_inputs: 0, max_outputs: 1,
                  outputs: [{ name: "out", type: "dataframe" }],
                  parameters: { dataset: { type: "dataset_ref", required: true } },
                },
              ],
              data_prep: [
                {
                  name: "split-data", component_type: "data_prep", label: "Split Data",
                  enabled: true, min_inputs: 1, max_inputs: 1, max_outputs: 2,
                  outputs: [{ name: "train", type: "dataframe" }, { name: "test", type: "dataframe" }],
                  parameters: {
                    split_size: { type: "number", minimum: 0.0, maximum: 1.0, required: true, default: 0.8 },
                    shuffle: { type: "boolean", required: false, default: true },
                  },
                },
              ],
            },
          },
        },
      };
    }
    if (req.path === "/api/v1/algorithm-templates" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            {
              name: "random_forest", label: "Random Forest", model_type: "classification", order: 14,
              input_type: { training: ["TRAIN"], tuning: ["TRAIN", "VALIDATION"], tuning_cross_validation: ["TRAIN"] },
              parameters: { n_estimators: { type: "int", minimum: 1, maximum: 2000, default: 200 } },
              runnable: true, metadata: { supervised: true },
            },
          ],
        },
      };
    }
    // list templates
    if (req.path === "/api/v1/pipelines" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "tpl-1", workspace_id: "ws-1", name: "Claims Retrain", pipeline_type: "training",
              active_version_id: "ver-1", is_system: false, archived: false,
              created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-10T00:00:00Z" },
          ],
          page: { next_cursor: "c2", has_more: true },
        },
      };
    }
    // create template
    if (req.path === "/api/v1/pipelines" && req.method === "POST") {
      return {
        status: 201,
        body: { data: { id: "tpl-new", workspace_id: req.body.workspace_id, name: req.body.name,
                        pipeline_type: req.body.pipeline_type, active_version_id: "ver-new",
                        validation_status: "draft", is_system: false, archived: false,
                        created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-10T00:00:00Z" } },
      };
    }
    // validate: valid vs invalid (422 with the report under `data`)
    if (req.path === "/api/v1/pipelines/validate" && req.method === "POST") {
      const nodes = (req.body?.definition?.nodes ?? []) as unknown[];
      if (nodes.length === 0) {
        return { status: 422, body: { data: { status: "draft", items: [
          { code: "EMPTY_DAG", alias: null, field: null, problem: "pipeline definition has no nodes" },
        ] } } };
      }
      return { status: 200, body: { data: { status: "valid", items: [] } } };
    }
    // run submission (202)
    if (req.path === "/api/v1/pipelines/tpl-1/run" && req.method === "POST") {
      return { status: 202, body: { operation_id: "op-1", data: { id: "run-1", template_id: "tpl-1",
              status: "submitted", created_at: "2026-07-10T00:00:00Z", started_at: null, finished_at: null } } };
    }
    // --- recurring pipeline schedules -----------------------------------------
    // list schedules (page_envelope)
    if (req.path === "/api/v1/pipeline-schedules" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "sch-1", template_id: "tpl-1", name: "Nightly Retrain", cron: "0 2 * * *",
              timezone: "UTC", run_parameters: { label_column: "label" }, enabled: true,
              next_fire_at: "2026-07-16T02:00:00Z", last_fire_at: null, last_run_id: null,
              created_by: "u-1", created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-10T00:00:00Z" },
          ],
          page: { next_cursor: null, has_more: false },
        },
      };
    }
    // create schedule (201)
    if (req.path === "/api/v1/pipeline-schedules" && req.method === "POST") {
      return { status: 201, body: { data: { id: "sch-new", template_id: req.body.template_id,
              name: req.body.name ?? null, cron: req.body.cron, timezone: req.body.timezone ?? "UTC",
              run_parameters: req.body.run_parameters ?? {}, enabled: true,
              next_fire_at: "2026-07-16T02:00:00Z", last_fire_at: null, last_run_id: null,
              created_by: "u-1", created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-10T00:00:00Z" } } };
    }
    // pause / resume
    if ((req.path === "/api/v1/pipeline-schedules/sch-1/pause"
      || req.path === "/api/v1/pipeline-schedules/sch-1/resume") && req.method === "POST") {
      const enabled = req.path.endsWith("/resume");
      return { status: 200, body: { data: { id: "sch-1", template_id: "tpl-1", name: "Nightly Retrain",
              cron: "0 2 * * *", timezone: "UTC", run_parameters: {}, enabled,
              next_fire_at: enabled ? "2026-07-16T02:00:00Z" : null, last_fire_at: null, last_run_id: null,
              created_by: "u-1", created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-10T00:00:00Z" } } };
    }
    // run-now (202): schedule under `data`, the created run under `run`
    if (req.path === "/api/v1/pipeline-schedules/sch-1/run-now" && req.method === "POST") {
      return { status: 202, body: {
        data: { id: "sch-1", template_id: "tpl-1", name: "Nightly Retrain", cron: "0 2 * * *",
              timezone: "UTC", run_parameters: {}, enabled: true, next_fire_at: "2026-07-16T02:00:00Z",
              last_fire_at: "2026-07-15T02:00:00Z", last_run_id: "run-7",
              created_by: "u-1", created_at: "2026-07-10T00:00:00Z", updated_at: "2026-07-10T00:00:00Z" },
        run: { id: "run-7", template_id: "tpl-1", status: "submitted",
              created_at: "2026-07-15T02:00:00Z", started_at: null, finished_at: null } } };
    }
    // delete (204)
    if (req.path === "/api/v1/pipeline-schedules/sch-1" && req.method === "DELETE") {
      return { status: 204, body: null };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("pipeline resolvers (pipeline-orchestrator passthrough, JWT forwarded)", () => {
  it("flattens the component catalog and reshapes params (dict→list, enumValues, min/max)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ pipelineStepTypes { name displayName category minInputs maxInputs maxOutputs outputs { name type } parameters { name type required default enumValues min max } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const types: any[] = (body?.data as any).pipelineStepTypes;
    expect(types.map((t) => t.name).sort()).toEqual(["read-from-warehouse", "split-data"]);
    const split = types.find((t) => t.name === "split-data");
    expect(split.displayName).toBe("Split Data");
    expect(split.category).toBe("data_prep");
    expect(split.maxOutputs).toBe(2);
    expect(split.outputs).toEqual([{ name: "train", type: "dataframe" }, { name: "test", type: "dataframe" }]);
    const byName = Object.fromEntries(split.parameters.map((p: any) => [p.name, p]));
    expect(byName.split_size).toMatchObject({ type: "number", required: true, default: 0.8, min: 0, max: 1 });
    expect(byName.shuffle.required).toBe(false);
    expect(requests[0]?.headers["authorization"]).toMatch(/^Bearer /);
  });

  it("maps algorithm templates (family + modes from input_type)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ algorithmTemplates { name displayName family modes parameters { name type } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const rf = (body?.data as any).algorithmTemplates[0];
    expect(rf.displayName).toBe("Random Forest");
    expect(rf.family).toBe("classification");
    expect(rf.modes).toEqual(["training", "tuning", "tuning_cross_validation"]);
  });

  it("lists pipeline templates cursor-paginated with a pipeline URN", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ pipelineTemplates(first: 10) { nodes { id name pipelineType urn } pageInfo { nextCursor hasMore } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const list = (body?.data as any).pipelineTemplates;
    expect(list.nodes[0].id).toBe("tpl-1");
    expect(list.nodes[0].pipelineType).toBe("training");
    expect(list.nodes[0].urn).toBe("wr:t-42:pipeline:template/tpl-1");
    expect(list.pageInfo).toEqual({ nextCursor: "c2", hasMore: true });
  });

  it("validatePipeline returns {valid:true} on 200 and {valid:false, issues} on 422 (report recovered from body)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);

    const okRes = await server.executeOperation(
      { query: `mutation($d: JSON!) { validatePipeline(definition: $d, pipelineType: "training") { valid issues { code message node } } }`,
        variables: { d: { nodes: [{ alias: "a", component: "read-from-warehouse" }], edges: [] } } },
      { contextValue: ctx },
    );
    const okBody = okRes.body.kind === "single" ? okRes.body.singleResult : null;
    expect(okBody?.errors).toBeUndefined();
    expect((okBody?.data as any).validatePipeline).toEqual({ valid: true, issues: [] });

    const badRes = await server.executeOperation(
      { query: `mutation($d: JSON!) { validatePipeline(definition: $d, pipelineType: "training") { valid issues { code message node } } }`,
        variables: { d: { nodes: [], edges: [] } } },
      { contextValue: ctx },
    );
    const badBody = badRes.body.kind === "single" ? badRes.body.singleResult : null;
    expect(badBody?.errors).toBeUndefined();
    const report = (badBody?.data as any).validatePipeline;
    expect(report.valid).toBe(false);
    expect(report.issues[0]).toEqual({ code: "EMPTY_DAG", message: "pipeline definition has no nodes", node: null });
  });

  it("createPipeline sources workspace_id from the JWT claim and forwards the idempotency key", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = pipeline();
    const ctx = await makeTestContext(fetchImpl, { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-9" });
    const res = await server.executeOperation(
      { query: `mutation($input: CreatePipelineInput!, $k: String!) { createPipeline(input: $input, idempotencyKey: $k) { id name pipelineType validationStatus } }`,
        variables: { input: { name: "New Pipe", pipelineType: "training", definition: { nodes: [], edges: [] } }, k: "idem-7" } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createPipeline.id).toBe("tpl-new");
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/pipelines");
    expect(post?.body.workspace_id).toBe("ws-9");
    expect(post?.body.pipeline_type).toBe("training");
    expect(post?.headers["idempotency-key"]).toBe("idem-7");
  });

  it("runPipeline submits a run and unwraps the run from the 202 envelope", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($p: JSON) { runPipeline(id: "tpl-1", input: { parameters: $p }) { id templateId status urn } }`,
        variables: { p: { label_column: "label" } } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const run = (body?.data as any).runPipeline;
    expect(run).toMatchObject({ id: "run-1", templateId: "tpl-1", status: "submitted", urn: "wr:t-42:pipeline:run/run-1" });
    const post = requests.find((r) => r.path === "/api/v1/pipelines/tpl-1/run" && r.method === "POST");
    expect(post?.body.run_parameters).toEqual({ label_column: "label" });
  });

  it("lists pipeline schedules (snake→camel, scheduleId + urn)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ pipelineSchedules { id scheduleId templateId name cron timezone runParameters enabled nextFireAt lastRunId urn } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const list = (body?.data as any).pipelineSchedules;
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({
      id: "sch-1", scheduleId: "sch-1", templateId: "tpl-1", name: "Nightly Retrain",
      cron: "0 2 * * *", timezone: "UTC", enabled: true, nextFireAt: "2026-07-16T02:00:00Z",
      urn: "wr:t-42:pipeline:schedule/sch-1",
    });
    expect(list[0].runParameters).toEqual({ label_column: "label" });
  });

  it("createPipelineSchedule maps the input and forwards the idempotency key", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation($input: CreatePipelineScheduleInput!, $k: String!) { createPipelineSchedule(input: $input, idempotencyKey: $k) { id templateId cron timezone enabled } }`,
        variables: { input: { templateId: "tpl-1", name: "Nightly", cron: "0 2 * * *", timezone: "UTC", runParameters: { label_column: "label" } }, k: "idem-9" } },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createPipelineSchedule.id).toBe("sch-new");
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/pipeline-schedules");
    expect(post?.body).toMatchObject({ template_id: "tpl-1", name: "Nightly", cron: "0 2 * * *", timezone: "UTC", run_parameters: { label_column: "label" } });
    expect(post?.headers["idempotency-key"]).toBe("idem-9");
  });

  it("pause/resume flip enabled", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const paused = await server.executeOperation(
      { query: `mutation { pausePipelineSchedule(id: "sch-1") { enabled } }` }, { contextValue: ctx },
    );
    const pb = paused.body.kind === "single" ? paused.body.singleResult : null;
    expect(pb?.errors).toBeUndefined();
    expect((pb?.data as any).pausePipelineSchedule.enabled).toBe(false);
    const resumed = await server.executeOperation(
      { query: `mutation { resumePipelineSchedule(id: "sch-1") { enabled } }` }, { contextValue: ctx },
    );
    const rb = resumed.body.kind === "single" ? resumed.body.singleResult : null;
    expect((rb?.data as any).resumePipelineSchedule.enabled).toBe(true);
  });

  it("runNowPipelineSchedule returns the created run from the sibling `run` key", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { runNowPipelineSchedule(id: "sch-1") { id templateId status urn } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).runNowPipelineSchedule).toMatchObject({
      id: "run-7", templateId: "tpl-1", status: "submitted", urn: "wr:t-42:pipeline:run/run-7",
    });
  });

  it("deletePipelineSchedule returns true on 204", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = pipeline();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { deletePipelineSchedule(id: "sch-1") }` }, { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deletePipelineSchedule).toBe(true);
  });
});

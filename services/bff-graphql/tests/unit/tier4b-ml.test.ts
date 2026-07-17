/**
 * Tier 4b: ml ops — experiment-service run tooling (register/best/compare/
 * notes/artifacts/metric-history/model cards) + inference-service job lifecycle
 * (cancel/retry/delete), validate and scoring schedules. Response shapes mirror
 * the real downstream route bodies — see
 * services/experiment-service/app/api/routes/{runs,experiments,models}.py and
 * services/inference-service/app/api/routes/{inferences,schedules}.py.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type Handler } from "../helpers/mockFetch.js";

const cfg = testConfig();
const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

const NOT_FOUND = { status: 404, body: { error: { code: "NOT_FOUND", message: "not found", trace_id: "t" } } };

function jobPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1", status: "running", name: "score claims", description: null,
    model: { urn: "wr:t:experiment:model_version/m-1@2", name: "claims", version: 2, stage_at_submit: "production" },
    input_dataset: { urn: "wr:t:dataset:dataset/ds-1", version: 3 },
    output_dataset: null, output_mode: 0, parameters: {}, compatibility_report: { compatible: true },
    pipeline_run_urn: null, components_status: {}, error: null, row_count: null,
    schedule_id: null, retried_from_job_id: null, via_agent: false,
    timestamps: { queued_at: null, submitted_at: "2026-07-12T01:00:00Z", started_at: "2026-07-12T01:00:05Z", finished_at: null, created_at: "2026-07-12T00:59:00Z" },
    ...overrides,
  };
}

function schedulePayload(overrides: Record<string, unknown> = {}) {
  return {
    id: "sch-1", name: "nightly scoring", enabled: true, paused_reason: null,
    model_version_urn: "wr:t:experiment:model_version/m-1@2", model_urn: null, stage_selector: null,
    input_selector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
    output: { dataset_name: "claims-scores", mode: "append" },
    cron: "0 6 * * *", interval_seconds: null, timezone: "UTC",
    // schemas.py schedule_payload serializes the raw OverlapPolicy IntEnum (0=skip).
    overlap_policy: 0, consecutive_failures: 0,
    temporal_schedule_id: "wr:t-42:inference:schedule/sch-1", notify_on_failure: true,
    next_fire_preview: { at: "2026-07-13T06:00:00Z" },
    ...overrides,
  };
}

async function run(handler: Handler, query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = mockFetch(handler);
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("ml ops: registerRunAsModel", () => {
  it("POSTs the snake_case register body and maps the result", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/experiments/exp-1/runs/run-1/register" && req.method === "POST") {
          return { status: 201, body: { data: { model_id: "m-9", version: 1, stage: "none", model_created: true } } };
        }
        return NOT_FOUND;
      },
      `mutation($k: String) {
        registerRunAsModel(experimentId: "exp-1", runId: "run-1",
          input: { modelName: "claims-model", description: "first cut", flavor: "mlflow.xgboost", ownerId: "u-7" },
          idempotencyKey: $k) { modelId version stage modelCreated }
      }`,
      { k: "idem-9" },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).registerRunAsModel).toEqual({
      modelId: "m-9", version: 1, stage: "none", modelCreated: true,
    });
    const post = requests.find((r) => r.method === "POST");
    expect(post?.body).toEqual({
      model_name: "claims-model", description: "first cut", flavor: "mlflow.xgboost", owner_id: "u-7",
    });
    expect(post?.headers["idempotency-key"]).toBe("idem-9");
  });

  it("surfaces RunNotFinished (409) verbatim — no fabricated version", async () => {
    const { body } = await run(
      () => ({
        status: 409,
        body: { error: { code: "RUN_NOT_FINISHED", message: "run must be finished to register (EXP-FR-031)", trace_id: "t" } },
      }),
      `mutation { registerRunAsModel(experimentId: "exp-1", runId: "run-2", input: { modelName: "claims-model" }) { modelId } }`,
    );
    expect(body?.data ?? null).toBeNull();
    expect(body?.errors?.[0]?.message).toContain("run must be finished to register");
  });
});

describe("ml ops: bestRun", () => {
  it("threads metric + direction + status as query params and folds metrics into the Run", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/experiments/exp-1/runs/best" && req.method === "GET") {
          return {
            status: 200,
            body: {
              data: {
                id: "run-3", urn: "wr:t:experiment:run/run-3", experiment_id: "exp-1",
                name: "trial-3", status: "finished", status_label: "Finished",
                algorithm: "xgboost", artifact_uri: null, duration_ms: 1000,
                started_at: null, ended_at: null, error_messages: null,
                created_at: "2026-07-10T00:00:00Z",
                metrics: { f1: 0.91, loss: 0.12 },
              },
            },
          };
        }
        return NOT_FOUND;
      },
      `{ bestRun(experimentId: "exp-1", metric: "f1", direction: "min", status: "finished") { id name status metrics } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).bestRun).toMatchObject({
      id: "run-3", name: "trial-3", status: "SUCCEEDED", metrics: { f1: 0.91, loss: 0.12 },
    });
    const get = requests.find((r) => r.path.endsWith("/runs/best"));
    expect(get?.search.get("metric")).toBe("f1");
    // The param is `direction` (NOT `mode`).
    expect(get?.search.get("direction")).toBe("min");
    expect(get?.search.get("status")).toBe("finished");
  });

  it("maps the no-run-with-metric 404 to null (not an error)", async () => {
    const { body } = await run(
      () => NOT_FOUND,
      `{ bestRun(experimentId: "exp-1", metric: "auc") { id } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).bestRun).toBeNull();
  });
});

describe("ml ops: compareRuns", () => {
  it("POSTs the run-id body and passes metric/param rows through verbatim", async () => {
    const metricsRows = [
      { key: "f1", values: { "run-1": 0.9, "run-2": 0.8 }, best_run_id: "run-1", direction: "max" },
    ];
    const paramsRows = [{ key: "max_depth", values: { "run-1": "6", "run-2": "8" }, differs: true }];
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/runs/compare" && req.method === "POST") {
          return {
            status: 200,
            body: {
              data: { runs: ["run-1", "run-2"], metrics: metricsRows, params: paramsRows },
              page: { next_cursor: null, has_more: false },
            },
          };
        }
        return NOT_FOUND;
      },
      `{ compareRuns(runIds: ["run-1", "run-2"], metrics: ["f1"], includeAll: false) { runIds metrics params } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).compareRuns).toEqual({
      runIds: ["run-1", "run-2"], metrics: metricsRows, params: paramsRows,
    });
    const post = requests.find((r) => r.method === "POST");
    expect(post?.body).toEqual({ run_ids: ["run-1", "run-2"], metrics: ["f1"], include_all: false });
  });
});

describe("ml ops: run notes", () => {
  it("upsertRunNote PUTs {description} and maps the echo", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/runs/run-1/note" && req.method === "PUT") {
          return { status: 200, body: { data: { run_id: "run-1", description: "great run" } } };
        }
        return NOT_FOUND;
      },
      `mutation { upsertRunNote(runId: "run-1", description: "great run") { runId description } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).upsertRunNote).toEqual({ runId: "run-1", description: "great run" });
    const put = requests.find((r) => r.method === "PUT");
    expect(put?.body).toEqual({ description: "great run" });
  });

  it("runNote maps the has-no-note 404 to null", async () => {
    const { body } = await run(() => NOT_FOUND, `{ runNote(runId: "run-1") { runId description } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).runNote).toBeNull();
  });

  it("deleteRunNote DELETEs and answers the real note_deleted flag", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/runs/run-1/note" && req.method === "DELETE") {
          return { status: 200, body: { data: { run_id: "run-1", note_deleted: true } } };
        }
        return NOT_FOUND;
      },
      `mutation { deleteRunNote(runId: "run-1") }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteRunNote).toBe(true);
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/runs/run-1/note")).toBe(true);
  });
});

describe("ml ops: artifacts + metric history", () => {
  it("runArtifacts maps the artifact index rows", async () => {
    const { body } = await run(
      (req) => {
        if (req.path === "/api/v1/runs/run-1/artifacts" && req.method === "GET") {
          return {
            status: 200,
            body: { data: [{ path: "model/model.pkl", size_bytes: 2048, content_type: "application/octet-stream" }] },
          };
        }
        return NOT_FOUND;
      },
      `{ runArtifacts(runId: "run-1") { path sizeBytes contentType } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).runArtifacts).toEqual([
      { path: "model/model.pkl", sizeBytes: 2048, contentType: "application/octet-stream" },
    ]);
  });

  it("runArtifactUrl threads the path param and returns the REAL signed url", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/runs/run-1/artifacts/url" && req.method === "GET") {
          return { status: 200, body: { data: { url: "https://minio.local/signed/abc?sig=x", path: req.search.get("path") } } };
        }
        return NOT_FOUND;
      },
      `{ runArtifactUrl(runId: "run-1", path: "model/model.pkl") }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).runArtifactUrl).toBe("https://minio.local/signed/abc?sig=x");
    const get = requests.find((r) => r.path.endsWith("/artifacts/url"));
    expect(get?.search.get("path")).toBe("model/model.pkl");
  });

  it("runMetricHistory threads keys as csv and passes rows through verbatim", async () => {
    const rows = [
      { key: "loss", step: 0, value: 0.9, logged_at: "2026-07-10T00:00:00Z" },
      { key: "loss", step: 1, value: 0.5, logged_at: "2026-07-10T00:01:00Z" },
    ];
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/runs/run-1/metric-history" && req.method === "GET") {
          return { status: 200, body: { data: rows, page: { next_cursor: null, has_more: false } } };
        }
        return NOT_FOUND;
      },
      `{ runMetricHistory(runId: "run-1", keys: ["loss", "f1"]) }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).runMetricHistory).toEqual(rows);
    const get = requests.find((r) => r.path.endsWith("/metric-history"));
    expect(get?.search.get("keys")).toBe("loss,f1");
  });
});

describe("ml ops: model cards", () => {
  it("updateModelCard PATCHes ONLY the set overlay fields and returns the merged card verbatim", async () => {
    const merged = {
      model_name: "claims", version: 2, stage: "production", algorithm: "xgboost",
      final_metrics: { f1: 0.91 }, training_data_unavailable: false,
      overlay: { intended_use: "claims triage", limitations: null, evaluation_summary: null, ethical_considerations: null },
    };
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/models/m-1/versions/2/card" && req.method === "PATCH") {
          return { status: 200, body: { data: merged } };
        }
        return NOT_FOUND;
      },
      `mutation { updateModelCard(modelId: "m-1", version: 2, input: { intendedUse: "claims triage" }) }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateModelCard).toEqual(merged);
    const patch = requests.find((r) => r.method === "PATCH");
    // Absent overlay fields must be ABSENT keys (exclude_unset), not nulls.
    expect(patch?.body).toEqual({ intended_use: "claims triage" });
  });

  it("modelCard maps a missing card 404 to null", async () => {
    const { body } = await run(() => NOT_FOUND, `{ modelCard(modelId: "m-1", version: 9) }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).modelCard).toBeNull();
  });
});

describe("ml ops: updateExperiment", () => {
  it("PATCHes only the provided fields and maps the experiment", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/experiments/exp-1" && req.method === "PATCH") {
          return {
            status: 200,
            body: { data: { id: "exp-1", name: "renamed", description: "new desc", archived: false } },
          };
        }
        return NOT_FOUND;
      },
      `mutation { updateExperiment(id: "exp-1", input: { name: "renamed", description: "new desc" }) { id name description } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateExperiment).toMatchObject({ id: "exp-1", name: "renamed", description: "new desc" });
    const patch = requests.find((r) => r.method === "PATCH");
    expect(patch?.body).toEqual({ name: "renamed", description: "new desc" });
    expect(patch?.body).not.toHaveProperty("note");
  });
});

describe("ml ops: inference job lifecycle", () => {
  it("cancelInferenceJob POSTs /cancel and maps the updated job", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/inferences/job-1/cancel" && req.method === "POST") {
          return { status: 200, body: { data: jobPayload({ status: "cancelling" }) } };
        }
        return NOT_FOUND;
      },
      `mutation { cancelInferenceJob(id: "job-1") { id status } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).cancelInferenceJob).toMatchObject({ id: "job-1", status: "cancelling" });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/inferences/job-1/cancel")).toBe(true);
  });

  it("retryInferenceJob follows the 202 with a GET of the NEW job id", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/inferences/job-1/retry" && req.method === "POST") {
          return { status: 202, body: { data: { operation_id: "job-2", job_id: "job-2" } } };
        }
        if (req.path === "/api/v1/inferences/job-2" && req.method === "GET") {
          return { status: 200, body: { data: jobPayload({ id: "job-2", status: "submitted", retried_from_job_id: "job-1" }) } };
        }
        return NOT_FOUND;
      },
      `mutation { retryInferenceJob(id: "job-1") { id status retriedFromJobId } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).retryInferenceJob).toMatchObject({
      id: "job-2", status: "submitted", retriedFromJobId: "job-1",
    });
    // The follow-up GET targets the NEW job id, not the retried one.
    expect(requests.some((r) => r.method === "GET" && r.path === "/api/v1/inferences/job-2")).toBe(true);
    expect(requests.some((r) => r.method === "GET" && r.path === "/api/v1/inferences/job-1")).toBe(false);
  });

  it("surfaces the non-terminal retry 409 verbatim", async () => {
    const { body } = await run(
      () => ({
        status: 409,
        body: { error: { code: "CONFLICT", message: "retry allowed only from a terminal failure state", trace_id: "t" } },
      }),
      `mutation { retryInferenceJob(id: "job-1") { id } }`,
    );
    expect(body?.data ?? null).toBeNull();
    expect(body?.errors?.[0]?.message).toContain("retry allowed only from a terminal failure state");
  });

  it("deleteInferenceJob DELETEs (204) and answers true", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/inferences/job-1" && req.method === "DELETE") return { status: 204 };
        return NOT_FOUND;
      },
      `mutation { deleteInferenceJob(id: "job-1") }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteInferenceJob).toBe(true);
    expect(requests.some((r) => r.method === "DELETE" && r.path === "/api/v1/inferences/job-1")).toBe(true);
  });
});

describe("ml ops: validateInference", () => {
  it("POSTs the snake_case body and maps the real compatibility report", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/inferences/validate" && req.method === "POST") {
          return {
            status: 200,
            body: {
              data: {
                compatible: false, model_stage: "staging",
                columns: [
                  { name: "claim_amount", required_type: "double", actual_type: "string", verdict: "type_mismatch" },
                  { name: "region", required_type: "string", actual_type: "string", verdict: "ok" },
                ],
                warnings: [{ code: "EXTRA_COLUMN", column: "notes" }],
                row_count: 1200, stage_error: "MODEL_NOT_PROMOTED",
              },
            },
          };
        }
        return NOT_FOUND;
      },
      `mutation {
        validateInference(input: { modelVersionUrn: "wr:t:experiment:model_version/m-1@2", inputDatasetUrn: "wr:t:dataset:dataset/ds-1", allowUnpromoted: true }) {
          compatible modelStage stageError rowCount warnings
          columns { name requiredType actualType verdict }
        }
      }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).validateInference).toEqual({
      compatible: false, modelStage: "staging", stageError: "MODEL_NOT_PROMOTED", rowCount: 1200,
      warnings: [{ code: "EXTRA_COLUMN", column: "notes" }],
      columns: [
        { name: "claim_amount", requiredType: "double", actualType: "string", verdict: "type_mismatch" },
        { name: "region", requiredType: "string", actualType: "string", verdict: "ok" },
      ],
    });
    const post = requests.find((r) => r.method === "POST");
    expect(post?.body).toEqual({
      model_version_urn: "wr:t:experiment:model_version/m-1@2",
      input_dataset_urn: "wr:t:dataset:dataset/ds-1",
      allow_unpromoted: true, allow_empty: false,
    });
  });
});

describe("ml ops: inference schedules", () => {
  it("createInferenceSchedule POSTs a cron-mode body (pinned model)", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules" && req.method === "POST") {
          return { status: 201, body: { data: schedulePayload() } };
        }
        return NOT_FOUND;
      },
      `mutation {
        createInferenceSchedule(input: {
          name: "nightly scoring",
          inputSelector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
          output: { dataset_name: "claims-scores", mode: "append" },
          modelVersionUrn: "wr:t:experiment:model_version/m-1@2",
          cron: "0 6 * * *", timezone: "UTC", overlapPolicy: "skip", notifyOnFailure: true
        }) { id name enabled cron intervalSeconds timezone overlapPolicy nextFireAt }
      }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createInferenceSchedule).toEqual({
      id: "sch-1", name: "nightly scoring", enabled: true, cron: "0 6 * * *",
      intervalSeconds: null, timezone: "UTC", overlapPolicy: "skip", nextFireAt: "2026-07-13T06:00:00Z",
    });
    const post = requests.find((r) => r.method === "POST");
    expect(post?.body).toEqual({
      name: "nightly scoring",
      input_selector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
      output: { dataset_name: "claims-scores", mode: "append" },
      model_version_urn: "wr:t:experiment:model_version/m-1@2",
      cron: "0 6 * * *", timezone: "UTC", overlap_policy: "skip", notify_on_failure: true,
    });
    // XOR fields the caller did not choose stay ABSENT (the service validates
    // exactly-one-of semantics on presence).
    expect(post?.body).not.toHaveProperty("interval_seconds");
    expect(post?.body).not.toHaveProperty("model_urn");
  });

  it("createInferenceSchedule POSTs an interval-mode body (stage-resolved model)", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules" && req.method === "POST") {
          return {
            status: 201,
            body: {
              data: schedulePayload({
                cron: null, interval_seconds: 3600,
                model_version_urn: null, model_urn: "wr:t:experiment:model/m-1", stage_selector: "production",
              }),
            },
          };
        }
        return NOT_FOUND;
      },
      `mutation {
        createInferenceSchedule(input: {
          name: "hourly scoring",
          inputSelector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
          output: { dataset_name: "claims-scores", mode: "append" },
          modelUrn: "wr:t:experiment:model/m-1", stageSelector: "production",
          intervalSeconds: 3600
        }) { id modelUrn stageSelector intervalSeconds cron }
      }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createInferenceSchedule).toMatchObject({
      modelUrn: "wr:t:experiment:model/m-1", stageSelector: "production", intervalSeconds: 3600, cron: null,
    });
    const post = requests.find((r) => r.method === "POST");
    expect(post?.body).toMatchObject({
      model_urn: "wr:t:experiment:model/m-1", stage_selector: "production", interval_seconds: 3600,
    });
    expect(post?.body).not.toHaveProperty("cron");
    expect(post?.body).not.toHaveProperty("model_version_urn");
  });

  it("updateInferenceSchedule PATCHes only the patchable fields", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules/sch-1" && req.method === "PATCH") {
          return { status: 200, body: { data: schedulePayload({ cron: "30 7 * * *", overlap_policy: 1 }) } };
        }
        return NOT_FOUND;
      },
      `mutation {
        updateInferenceSchedule(id: "sch-1", input: { cron: "30 7 * * *", overlapPolicy: "queue" }) { id cron overlapPolicy }
      }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateInferenceSchedule).toMatchObject({ cron: "30 7 * * *", overlapPolicy: "queue" });
    const patch = requests.find((r) => r.method === "PATCH");
    expect(patch?.body).toEqual({ cron: "30 7 * * *", overlap_policy: "queue" });
  });

  it("pause / resume POST the lifecycle routes and map the schedule", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules/sch-1/pause" && req.method === "POST") {
          return {
            status: 200,
            body: { data: schedulePayload({ enabled: false, paused_reason: "USER_PAUSED", next_fire_preview: { at: null } }) },
          };
        }
        return NOT_FOUND;
      },
      `mutation { pauseInferenceSchedule(id: "sch-1") { id enabled pausedReason nextFireAt } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).pauseInferenceSchedule).toEqual({
      id: "sch-1", enabled: false, pausedReason: "USER_PAUSED", nextFireAt: null,
    });
    expect(requests.some((r) => r.path === "/api/v1/schedules/sch-1/pause" && r.method === "POST")).toBe(true);

    const resumed = await run(
      (req) => {
        if (req.path === "/api/v1/schedules/sch-1/resume" && req.method === "POST") {
          return { status: 200, body: { data: schedulePayload({ enabled: true, consecutive_failures: 0 }) } };
        }
        return NOT_FOUND;
      },
      `mutation { resumeInferenceSchedule(id: "sch-1") { id enabled consecutiveFailures } }`,
    );
    expect(resumed.body?.errors).toBeUndefined();
    expect((resumed.body?.data as any).resumeInferenceSchedule).toEqual({
      id: "sch-1", enabled: true, consecutiveFailures: 0,
    });
  });

  it("triggerInferenceSchedule answers the real fire result verbatim", async () => {
    const { body } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules/sch-1/trigger" && req.method === "POST") {
          return { status: 202, body: { data: { fired: true, job_id: "job-7", status: 2 } } };
        }
        return NOT_FOUND;
      },
      `mutation { triggerInferenceSchedule(id: "sch-1") }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).triggerInferenceSchedule).toEqual({ fired: true, job_id: "job-7", status: 2 });
  });

  it("deleteInferenceSchedule DELETEs (204) and answers true", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules/sch-1" && req.method === "DELETE") return { status: 204 };
        return NOT_FOUND;
      },
      `mutation { deleteInferenceSchedule(id: "sch-1") }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).deleteInferenceSchedule).toBe(true);
    expect(requests.some((r) => r.method === "DELETE")).toBe(true);
  });

  it("inferenceSchedules lists + inferenceScheduleFires maps the schedule's job history", async () => {
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/schedules" && req.method === "GET") {
          return { status: 200, body: { data: [schedulePayload()], page: { next_cursor: null, has_more: false } } };
        }
        if (req.path === "/api/v1/schedules/sch-1/fires" && req.method === "GET") {
          return {
            status: 200,
            body: {
              data: [jobPayload({ id: "job-9", status: "succeeded", schedule_id: "sch-1", row_count: 42 })],
              page: { next_cursor: null, has_more: false },
            },
          };
        }
        return NOT_FOUND;
      },
      `{
        inferenceSchedules { nodes { id name enabled overlapPolicy inputSelector } pageInfo { hasMore } }
        inferenceScheduleFires(scheduleId: "sch-1") { nodes { id status scheduleId rowCount } }
      }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).inferenceSchedules.nodes[0]).toMatchObject({
      id: "sch-1", name: "nightly scoring", enabled: true, overlapPolicy: "skip",
      inputSelector: { dataset_urn: "wr:t:dataset:dataset/ds-1" },
    });
    expect((body?.data as any).inferenceScheduleFires.nodes[0]).toEqual({
      id: "job-9", status: "succeeded", scheduleId: "sch-1", rowCount: 42,
    });
    expect(requests.some((r) => r.path === "/api/v1/schedules/sch-1/fires")).toBe(true);
  });
});

describe("ml ops: bulkCreateInferenceJobs", () => {
  it("POSTs /inferences/bulk and answers the per-dataset partial-failure list verbatim", async () => {
    const results = [
      { input_dataset_urn: "wr:t:dataset:dataset/ds-1", job_id: "job-1", status: "submitted" },
      { input_dataset_urn: "wr:t:dataset:dataset/ds-2", error: { code: "VALIDATION_FAILED", message: "duplicate name" } },
    ];
    const { body, requests } = await run(
      (req) => {
        if (req.path === "/api/v1/inferences/bulk" && req.method === "POST") {
          return { status: 200, body: { data: results } };
        }
        return NOT_FOUND;
      },
      `mutation {
        bulkCreateInferenceJobs(input: {
          modelVersionUrn: "wr:t:experiment:model_version/m-1@2",
          inputDatasetUrns: ["wr:t:dataset:dataset/ds-1", "wr:t:dataset:dataset/ds-2"],
          outputDatasetName: "scores", outputMode: "append"
        })
      }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).bulkCreateInferenceJobs).toEqual(results);
    const post = requests.find((r) => r.method === "POST");
    expect(post?.body).toEqual({
      model_version_urn: "wr:t:experiment:model_version/m-1@2",
      input_dataset_urns: ["wr:t:dataset:dataset/ds-1", "wr:t:dataset:dataset/ds-2"],
      output: { dataset_name: "scores", mode: "append" },
    });
  });
});

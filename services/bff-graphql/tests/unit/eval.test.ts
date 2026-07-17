/**
 * eval-service: suites, runs (+ nested suite/gate field resolvers), the case
 * curation queue, and trends. Response shapes mirror the real downstream route
 * bodies — see services/eval-service/app/api/routes/{suites,runs,cases,gates,trends}.py.
 * NB: eval-service's list envelope is FLAT ({data, next_cursor, has_more}), unlike
 * every other service's {data, page:{...}} nesting — see clients/eval.ts adaptPage.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function eval_() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/suites" && req.method === "POST") {
      expect(req.body).toMatchObject({ suite_id: "nl2sql", agent_key: "claims-agent" });
      return {
        status: 201,
        body: {
          data: {
            id: "su-1", suite_id: "nl2sql", agent_key: "claims-agent", version: 1,
            datasets: [{ dataset_key: "claims-agent/nl2sql", version: 1 }],
            scorers: [{ scorer: "exact_match", version: 1, weight: 1, regression_threshold: 0.05 }],
            gate_rule: "exact_match.pass_rate >= 0.9", baseline_version: null,
            judge_ladder_pin: {}, min_cases: 0, created_at: "2026-07-01T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/suites/nl2sql" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: {
            id: "su-1", suite_id: "nl2sql", agent_key: "claims-agent", version: 1,
            datasets: [{ dataset_key: "claims-agent/nl2sql", version: 1 }],
            scorers: [{ scorer: "exact_match", version: 1 }], gate_rule: "exact_match.pass_rate >= 0.9",
            baseline_version: null, judge_ladder_pin: {}, min_cases: 0, created_at: "2026-07-01T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/suites/nl2sql" && req.method === "PATCH") {
      return {
        status: 200,
        body: {
          data: {
            id: "su-1", suite_id: "nl2sql", agent_key: "claims-agent", version: 1,
            datasets: [{ dataset_key: "claims-agent/nl2sql", version: 1 }],
            scorers: [{ scorer: "exact_match", version: 1 }],
            gate_rule: req.body.gate_rule ?? "exact_match.pass_rate >= 0.9",
            baseline_version: req.body.baseline_version ?? null, judge_ladder_pin: {},
            min_cases: req.body.min_cases ?? 0, created_at: "2026-07-01T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/scorers/exact_match" && req.method === "PATCH") {
      return {
        status: 200,
        body: {
          data: {
            id: "sc-1", scorer_key: "exact_match", version: 2, kind: "deterministic",
            gate_eligible: req.body.gate_eligible ?? true,
            config_schema: req.body.config_schema ?? {}, applicable_expected_kinds: [],
            status: req.body.status ?? "draft", created_at: "2026-07-01T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/runs" && req.method === "POST") {
      expect(req.body).toMatchObject({ agent_key: "claims-agent", suite_id: "nl2sql" });
      return {
        status: 201,
        body: {
          data: {
            id: "run-1", trigger: "manual", agent_key: "claims-agent",
            candidate: { content_digest: "sha256:abc" }, baseline: null,
            suite_pins: {
              suite_id: "nl2sql", suite_version: 1,
              datasets: [{ dataset_key: "claims-agent/nl2sql", version: 1 }],
            },
            memory_snapshot_ver: null, status: "completed",
            totals: { aggregates: { exact_match: { mean: 0.92, pass_rate: 0.92 } } },
            cost_usd: 0.14, cost_cap_usd: 5.0, started_by: "u-1",
            created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:01Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/runs" && req.method === "GET") {
      expect(req.search.get("agent_key")).toBe("claims-agent");
      return {
        status: 200,
        body: {
          data: [
            {
              id: "run-1", trigger: "manual", agent_key: "claims-agent",
              candidate: { content_digest: "sha256:abc" }, baseline: null,
              suite_pins: { suite_id: "nl2sql", suite_version: 1 },
              status: "completed", totals: {}, cost_usd: 0.14, cost_cap_usd: 5.0,
              started_by: "u-1", created_at: "2026-07-12T00:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        },
      };
    }
    if (req.path === "/api/v1/runs/run-1" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: {
            id: "run-1", trigger: "manual", agent_key: "claims-agent",
            candidate: { content_digest: "sha256:abc" }, baseline: null,
            suite_pins: {
              suite_id: "nl2sql", suite_version: 1,
              datasets: [{ dataset_key: "claims-agent/nl2sql", version: 1 }],
            },
            status: "completed", totals: {}, cost_usd: 0.14, cost_cap_usd: 5.0,
            started_by: "u-1", created_at: "2026-07-12T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/runs/run-1/cases" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            {
              id: "cr-1", run_id: "run-1", case_id: "c-1", scorer_key: "exact_match", scorer_version: 1,
              score: 1.0, passed: true, details: {}, trace_ref: null, latency_ms: 120, cost_usd: 0.01,
              weight: 1, created_at: "2026-07-12T00:00:00Z",
            },
          ],
        },
      };
    }
    if (req.path === "/api/v1/gates" && req.method === "GET") {
      expect(req.search.get("agent_key")).toBe("claims-agent");
      expect(req.search.get("content_digest")).toBe("sha256:abc");
      return {
        status: 200,
        body: {
          data: [
            {
              id: "g-1", gate_run_id: "gr-1", run_id: "run-1", agent_key: "claims-agent",
              content_digest: "sha256:abc", suite_id: "nl2sql", suite_version: 1, dataset_version: 1,
              gate_passed: true, verdicts: [{ scorer: "exact_match@1", passed: true }],
              failed_cases_sample: [], report_url: "/api/v1/runs/run-1", created_at: "2026-07-12T00:00:00Z",
            },
          ],
        },
      };
    }
    if (req.path === "/api/v1/cases" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            {
              id: "c-1", dataset_key: "claims-agent/nl2sql", dataset_version: 1,
              input: { messages: [] }, expected: { kind: "sql_result", value: {} },
              source: "manual", source_ref: null, tags: [], weight: 1, status: "candidate",
              anonymization_attested_by: null, created_at: "2026-07-12T00:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        },
      };
    }
    if (req.path === "/api/v1/cases/c-1/promote" && req.method === "POST") {
      return {
        status: 200,
        body: {
          data: {
            id: "c-1", dataset_key: "claims-agent/nl2sql", dataset_version: 1,
            input: {}, expected: {}, source: "manual", tags: [], weight: 1, status: "active",
            created_at: "2026-07-12T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/trends" && req.method === "GET") {
      expect(req.search.get("agent_key")).toBe("claims-agent");
      return {
        status: 200,
        body: {
          data: [
            { run_id: "run-1", agent_version: "v3", scorer: "exact_match", mean: 0.92, pass_rate: 0.92, at: "2026-07-12T00:00:00Z" },
          ],
        },
      };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"] };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = eval_();
  const ctx = await makeTestContext(fetchImpl, CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("eval: suites", () => {
  it("createEvalSuite POSTs and returns the suite", async () => {
    const { body } = await run(
      `mutation($input: CreateEvalSuiteInput!){ createEvalSuite(input: $input) { id suiteId agentKey version gateRule } }`,
      { input: { suiteId: "nl2sql", agentKey: "claims-agent", datasets: [{ dataset_key: "claims-agent/nl2sql", version: 1 }], scorers: [{ scorer: "exact_match", version: 1 }], gateRule: "exact_match.pass_rate >= 0.9" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createEvalSuite).toMatchObject({ id: "su-1", suiteId: "nl2sql", version: 1 });
  });

  it("evalSuite reads by suiteId", async () => {
    const { body } = await run(`{ evalSuite(suiteId: "nl2sql") { suiteId gateRule minCases } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalSuite).toMatchObject({ suiteId: "nl2sql", minCases: 0 });
  });

  it("updateEvalSuite PATCHes only the provided fields (snake_case wire body)", async () => {
    const { body, requests } = await run(
      `mutation($input: UpdateEvalSuiteInput!){ updateEvalSuite(input: $input) { id suiteId minCases baselineVersion } }`,
      { input: { suiteId: "nl2sql", minCases: 5, baselineVersion: "v7" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateEvalSuite).toMatchObject({ id: "su-1", suiteId: "nl2sql", minCases: 5, baselineVersion: "v7" });
    const patch = requests.find((r) => r.path === "/api/v1/suites/nl2sql" && r.method === "PATCH");
    // datasets/scorers/gateRule/judgeLadderPin omitted → not sent (partial update).
    expect(patch?.body).toEqual({ min_cases: 5, baseline_version: "v7" });
  });
});

describe("eval: scorers", () => {
  it("updateEvalScorer PATCHes only the provided fields (snake_case wire body)", async () => {
    const { body, requests } = await run(
      `mutation($input: UpdateEvalScorerInput!){ updateEvalScorer(input: $input) { id scorerKey status gateEligible configSchema } }`,
      { input: { scorerKey: "exact_match", status: "active", configSchema: { threshold: 0.9 } } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).updateEvalScorer).toMatchObject({
      id: "sc-1", scorerKey: "exact_match", status: "active", configSchema: { threshold: 0.9 },
    });
    const patch = requests.find((r) => r.path === "/api/v1/scorers/exact_match" && r.method === "PATCH");
    // kind/scorerKey immutable, gateEligible/imageRef/etc omitted → not sent.
    expect(patch?.body).toEqual({ status: "active", config_schema: { threshold: 0.9 } });
  });
});

describe("eval: runs (with nested suite + gate resolvers)", () => {
  it("createEvalRun executes synchronously and returns the completed run", async () => {
    const { body } = await run(
      `mutation($input: CreateEvalRunInput!){ createEvalRun(input: $input) { id status costUsd totals } }`,
      { input: { agentKey: "claims-agent", candidate: { content_digest: "sha256:abc" }, suiteId: "nl2sql" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createEvalRun).toMatchObject({ id: "run-1", status: "completed" });
  });

  it("evalRuns lists (adapting the flat next_cursor/has_more envelope)", async () => {
    const { body } = await run(`{ evalRuns(agentKey: "claims-agent") { nodes { id status } pageInfo { hasMore } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalRuns.nodes[0]).toMatchObject({ id: "run-1", status: "completed" });
  });

  it("EvalRun.cases hydrates per-case scorer verdicts", async () => {
    const { body } = await run(`{ evalRun(id: "run-1") { cases { caseId scorerKey score passed } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalRun.cases[0]).toMatchObject({ caseId: "c-1", scorerKey: "exact_match", passed: true });
  });

  it("EvalRun.suite resolves the pinned suite by id+version from suitePins", async () => {
    const { body, requests } = await run(`{ evalRun(id: "run-1") { suite { suiteId version } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalRun.suite).toMatchObject({ suiteId: "nl2sql", version: 1 });
    expect(requests.some((r) => r.path === "/api/v1/suites/nl2sql" && r.method === "GET")).toBe(true);
  });

  it("EvalRun.gate matches by agentKey+contentDigest+suite/dataset pins (the CI dedup lookup)", async () => {
    const { body } = await run(`{ evalRun(id: "run-1") { gate { gateRunId gatePassed } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalRun.gate).toMatchObject({ gateRunId: "gr-1", gatePassed: true });
  });
});

describe("eval: case curation queue", () => {
  it("evalCases lists the candidate queue by default", async () => {
    const { body } = await run(`{ evalCases { nodes { id status source } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalCases.nodes[0]).toMatchObject({ id: "c-1", status: "candidate" });
  });

  it("promoteEvalCase POSTs the promote action", async () => {
    const { body, requests } = await run(`mutation{ promoteEvalCase(id: "c-1") { id status } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).promoteEvalCase).toMatchObject({ id: "c-1", status: "active" });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/cases/c-1/promote")).toBe(true);
  });
});

describe("eval: trends (model-version scorecard data)", () => {
  it("evalTrends returns the score series for an agent", async () => {
    const { body } = await run(`{ evalTrends(agentKey: "claims-agent") { scorer mean passRate agentVersion } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).evalTrends[0]).toMatchObject({ scorer: "exact_match", mean: 0.92, agentVersion: "v3" });
  });
});

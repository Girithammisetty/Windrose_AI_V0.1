/**
 * E2E contract server — the "near-real HTTP contract" fallback the task permits.
 *
 * It plays TWO real roles for the booted real bff-graphql + the app:
 *  1) The domain services' REST surface (OpenAPI-shaped bodies, exact DTO field
 *     names the BFF clients map). The BFF's real undici client calls these.
 *  2) A realtime-hub speaking the REAL hub wire protocol: POST /api/v1/stream-tickets
 *     mints a single-use ticket; GET /api/v1/stream?ticket= streams SSE frames in
 *     the exact `event: control {type:connected,conn_id}` + `event:<topic>` format
 *     the app's SSE client (and the real hub) use. Real EventSource, real SSE.
 *
 * No fake code runs inside the BFF or the app — this is the external system under
 * contract, exactly like bff-graphql's own realDownstream integration test.
 */
import http from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.CONTRACT_PORT ?? 4600);
const TENANT = "t-acme";
const caseUrn = (id) => `wr:${TENANT}:case:case/${id}`;

/* ---------------- fixtures ---------------- */
const users = {
  "user-1": { id: "user-1", email: "ann@acme.com", full_name: "Ann Adjuster", status: "active" },
};
const datasets = {
  "ds-9": { id: "ds-9", name: "claims-2026-q1", description: "Q1 claims", status: "ready", tags: ["claims"], row_count: 1250000, created_at: "2026-01-04T10:00:00Z" },
};
const cases = {
  "case-1": {
    id: "case-1", case_number: 4471, title: "Suspicious auto claim #4471",
    status: "in_progress", severity: "high", assigned_to_id: "user-1",
    dataset_urn: `wr:${TENANT}:dataset:dataset/ds-9`, due_date: "2026-07-20T00:00:00Z",
    created_at: "2026-07-01T09:00:00Z",
  },
};
// Proposals: one benign (bulk-approvable) + one destructive (excluded from bulk).
function seedProposals() {
  return {
  "prop-assign": {
    id: "prop-assign", agent_key: "triage-agent@v3", tool: "assign_case",
    args_diff: { before: { assignee: null }, after: { assignee: "user-1" } },
    rationale: "Fraud score 0.87 exceeds the auto-review threshold; assign to the fraud desk.",
    affected_urns: [caseUrn("case-1")], predicted_effect: "Case assigned to Ann Adjuster",
    status: "pending", resource_urn: caseUrn("case-1"), created_at: "2026-07-09T12:00:00Z",
  },
  "prop-delete": {
    id: "prop-delete", agent_key: "cleanup-agent@v1", tool: "delete_case",
    args_diff: { before: { id: "case-1" }, after: {} },
    rationale: "Duplicate of case-1.", affected_urns: [caseUrn("case-1")],
    predicted_effect: "Case permanently deleted", status: "pending",
    resource_urn: caseUrn("case-1"), created_at: "2026-07-09T13:00:00Z",
  },
  };
}
let proposals = seedProposals();
const agentRuns = {
  "run-1": {
    id: "run-1", agent_key: "triage-agent@v3", status: "succeeded", cost_usd: 0.0182,
    token_usage: { input_tokens: 5120, output_tokens: 640 }, created_at: "2026-07-09T12:00:00Z",
  },
};
const trace = {
  id: "root", name: "triage-agent", type: "agent", status: "ok", duration_ms: 1840,
  children: [
    { id: "s1", name: "load case context", type: "step", status: "ok", duration_ms: 120 },
    { id: "s2", name: "tool: fraud_score", type: "tool_call", status: "ok", duration_ms: 900, tokens: 320,
      citations: [{ urn: `wr:${TENANT}:dataset:dataset/ds-9`, label: "claims-2026-q1" }] },
    { id: "s3", name: "tool: notify_desk", type: "tool_call", status: "error", duration_ms: 40,
      error: "desk webhook 503" },
  ],
};
const dashboards = {
  "dash-1": { id: "dash-1", workspace_id: "ws-claims", title: "Fraud overview", module: "cases", chart_ids: ["chart-1"] },
};
const charts = {
  "chart-1": {
    id: "chart-1", dashboard_id: "dash-1", name: "Claims by severity", chart_type: "bar",
    spec: { measure: "count", dimension: "severity" },
    // AI provenance → drives the AC-4 provenance badge.
    provenance: { agent: "analytics-agent@v2", version: "v2", sourceRunId: "run-1", approvedBy: "ann@acme.com", timestamp: "2026-07-08T00:00:00Z" },
  },
};
const chartData = {
  "chart-1": { chart_id: "chart-1", columns: ["severity", "count"], rows: [["high", 42], ["medium", 118], ["low", 305]], meta: {} },
};
const experiments = { "exp-1": { id: "exp-1", name: "fraud-xgb", description: "XGBoost fraud model" } };
const runs = { "mrun-1": { id: "mrun-1", name: "trial-7", status: "succeeded", metrics: { auc: 0.94, f1: 0.88 }, params: { max_depth: 6 }, model_id: "model-1" } };
const models = { "model-1": { id: "model-1", name: "fraud-xgb", stage: "production" } };

/* ---------------- helpers ---------------- */
function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json", "x-trace-id": "contract-" + randomUUID().slice(0, 8) });
  res.end(JSON.stringify(body));
}
const page = (data, next = null) => ({ data, page: { next_cursor: next, has_more: !!next } });
async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const s = Buffer.concat(chunks).toString("utf8");
  try { return s ? JSON.parse(s) : {}; } catch { return {}; }
}

/* ---------------- server ---------------- */
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const p = url.pathname;
  const q = url.searchParams;

  // ---- realtime-hub: mint ticket (bearer-authed; we don't verify here) ----
  if (req.method === "POST" && p === "/api/v1/stream-tickets") {
    const body = await readBody(req);
    const topics = Array.isArray(body.topics) ? body.topics : [];
    const ticket = Buffer.from(JSON.stringify(topics)).toString("base64url");
    return send(res, 200, { ticket });
  }

  // ---- realtime-hub: SSE stream (real wire format) ----
  if (req.method === "GET" && p === "/api/v1/stream") {
    const ticket = q.get("ticket") ?? "";
    let topics = [];
    try { topics = JSON.parse(Buffer.from(ticket, "base64url").toString("utf8")); } catch { topics = []; }
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
      "x-accel-buffering": "no",
      // The browser opens this EventSource cross-origin (app:3100 → hub:4600).
      "access-control-allow-origin": "*",
    });
    const connId = randomUUID();
    res.write(`event: control\ndata: {"type":"connected","conn_id":"${connId}"}\n\n`);

    // For a chat subscription, stream assistant tokens then an action + done.
    const chatTopic = topics.find((t) => String(t).startsWith("chat:"));
    if (chatTopic) {
      const tokens = ["The ", "fraud ", "score ", "for ", "this ", "claim ", "is ", "high. ", "I recommend ", "assigning ", "it ", "to ", "the ", "fraud ", "desk."];
      let i = 0;
      const iv = setInterval(() => {
        if (i < tokens.length) {
          res.write(`event: ${chatTopic}\ndata: ${JSON.stringify({ type: "token", text: tokens[i++] })}\n\n`);
        } else {
          res.write(`event: ${chatTopic}\ndata: ${JSON.stringify({ type: "citation", urn: caseUrn("case-1"), label: "Case #4471" })}\n\n`);
          res.write(`event: ${chatTopic}\ndata: ${JSON.stringify({ type: "action", label: "Review proposal", proposalId: "prop-assign" })}\n\n`);
          res.write(`event: ${chatTopic}\ndata: ${JSON.stringify({ type: "done" })}\n\n`);
          clearInterval(iv);
        }
      }, 60);
      req.on("close", () => clearInterval(iv));
    } else {
      // Status topics: keepalive heartbeats.
      const hb = setInterval(() => res.write(`event: control\ndata: {"type":"heartbeat"}\n\n`), 15000);
      req.on("close", () => clearInterval(hb));
    }
    return;
  }

  // ---- copilot chat entry (agent-runtime) ----
  if (req.method === "POST" && p === "/api/v1/chat") {
    const body = await readBody(req);
    const threadId = body.thread_id || randomUUID();
    return send(res, 200, { thread_id: threadId, run_id: "run-1", topics: [`chat:${threadId}`] });
  }

  // ---- identity-service ----
  if (p === "/api/v1/users") {
    const ids = q.get("filter[id]");
    const list = ids ? ids.split(",").map((id) => users[id]).filter(Boolean) : Object.values(users);
    return send(res, 200, page(list));
  }
  const userM = p.match(/^\/api\/v1\/users\/(.+)$/);
  if (userM) return users[userM[1]] ? send(res, 200, users[userM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "no user", trace_id: "t" } });

  // ---- dataset-service ----
  if (p === "/api/v1/datasets") {
    const ids = q.get("filter[id]");
    const list = ids ? ids.split(",").map((id) => datasets[id]).filter(Boolean) : Object.values(datasets);
    return send(res, 200, page(list));
  }
  const dsProfile = p.match(/^\/api\/v1\/datasets\/(.+)\/profile$/);
  if (dsProfile) return send(res, 200, { row_count: 1250000, column_count: 42, full_json_url: "http://x/p.json", html_report_url: "http://x/p.html" });
  const dsM = p.match(/^\/api\/v1\/datasets\/(.+)$/);
  if (dsM) return datasets[dsM[1]] ? send(res, 200, datasets[dsM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "no dataset", trace_id: "t" } });

  // ---- case-service ----
  if (p === "/api/v1/cases") {
    const ids = q.get("filter[id]");
    const list = ids ? ids.split(",").map((id) => cases[id]).filter(Boolean) : Object.values(cases);
    return send(res, 200, page(list));
  }
  const caseM = p.match(/^\/api\/v1\/cases\/(.+)$/);
  if (caseM) {
    if (req.method === "PATCH") {
      const body = await readBody(req);
      cases[caseM[1]] = { ...cases[caseM[1]], ...body };
      return send(res, 200, cases[caseM[1]]);
    }
    return cases[caseM[1]] ? send(res, 200, cases[caseM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "no case", trace_id: "t" } });
  }

  // ---- agent-runtime ----
  if (p === "/api/v1/proposals") {
    const resourceUrn = q.get("filter[resource_urn]");
    const status = q.get("filter[status]");
    let list = Object.values(proposals);
    if (resourceUrn) list = list.filter((pr) => resourceUrn.split(",").includes(pr.resource_urn));
    if (status) list = list.filter((pr) => pr.status === status);
    return send(res, 200, page(list));
  }
  const decideM = p.match(/^\/api\/v1\/proposals\/(.+)\/decide$/);
  if (decideM && req.method === "POST") {
    const body = await readBody(req);
    const pr = proposals[decideM[1]];
    if (!pr) return send(res, 404, { error: { code: "NOT_FOUND", message: "no proposal", trace_id: "t" } });
    const status = body.action === "approve" ? "approved" : body.action === "reject" ? "rejected" : body.action === "edit_args" ? "edited_approved" : "responded";
    proposals[decideM[1]] = { ...pr, status, decision: { action: body.action, message: body.message } };
    return send(res, 200, proposals[decideM[1]]);
  }
  const propM = p.match(/^\/api\/v1\/proposals\/(.+)$/);
  if (propM) return proposals[propM[1]] ? send(res, 200, proposals[propM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "no proposal", trace_id: "t" } });
  const runTraceM = p.match(/^\/api\/v1\/runs\/(.+)\/trace$/);
  if (runTraceM) return send(res, 200, trace);
  const runM = p.match(/^\/api\/v1\/runs\/(.+)$/);
  if (runM && agentRuns[runM[1]]) return send(res, 200, agentRuns[runM[1]]);

  // ---- experiment-service ----
  if (p === "/api/v1/experiments") return send(res, 200, page(Object.values(experiments)));
  const expRunsM = p.match(/^\/api\/v1\/experiments\/(.+)\/runs$/);
  if (expRunsM) return send(res, 200, page(Object.values(runs)));
  const expM = p.match(/^\/api\/v1\/experiments\/(.+)$/);
  if (expM) return experiments[expM[1]] ? send(res, 200, experiments[expM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
  if (p === "/api/v1/runs") return send(res, 200, page(Object.values(runs)));
  if (p === "/api/v1/models") return send(res, 200, page(Object.values(models)));
  const mrunM = p.match(/^\/api\/v1\/runs\/(.+)$/);
  if (mrunM && runs[mrunM[1]]) return send(res, 200, runs[mrunM[1]]);
  const modelM = p.match(/^\/api\/v1\/models\/(.+)$/);
  if (modelM) return models[modelM[1]] ? send(res, 200, models[modelM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });

  // ---- chart-service ----
  if (p === "/api/v1/dashboards") return send(res, 200, page(Object.values(dashboards)));
  const dashDataM = p.match(/^\/api\/v1\/dashboards\/(.+)\/data$/);
  if (dashDataM && req.method === "POST") return send(res, 200, { data: Object.values(chartData) });
  const dashM = p.match(/^\/api\/v1\/dashboards\/(.+)$/);
  if (dashM) {
    const d = dashboards[dashM[1]];
    if (!d) return send(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
    return send(res, 200, { ...d, charts: (d.chart_ids ?? []).map((id) => charts[id]).filter(Boolean) });
  }
  const chartDataM = p.match(/^\/api\/v1\/charts\/(.+)\/data$/);
  if (chartDataM) return send(res, 200, chartData[chartDataM[1]] ?? { rows: [], columns: [] });
  const chartM = p.match(/^\/api\/v1\/charts\/(.+)$/);
  if (chartM) return charts[chartM[1]] ? send(res, 200, charts[chartM[1]]) : send(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });

  // ---- usage-service ----
  if (p === "/api/v1/reports/usage") {
    return send(res, 200, {
      rows: [
        { dimensions: { model: "gpt-4o" }, meter_key: "llm.tokens.input", quantity: 512000, cost_usd: 5.12 },
        { dimensions: { model: "gpt-4o" }, meter_key: "llm.tokens.output", quantity: 64000, cost_usd: 1.92 },
      ],
    });
  }
  if (p === "/api/v1/budget-states") {
    return send(res, 200, page([
      { scope: "workspace:ws-claims", consumed: 71.4, limit: 100, last_threshold: 80, exhausted_at: null },
    ]));
  }
  if (p === "/api/v1/budgets") return send(res, 200, page([]));

  // Test isolation: reset mutable proposal state between specs.
  if (req.method === "POST" && p === "/__reset") {
    proposals = seedProposals();
    return send(res, 200, { ok: true });
  }
  if (p === "/healthz") return send(res, 200, { ok: true });
  return send(res, 404, { error: { code: "NOT_FOUND", message: `no route ${p}`, trace_id: "t" } });
});

server.listen(PORT, () => console.log(`[contract] listening on :${PORT}`));

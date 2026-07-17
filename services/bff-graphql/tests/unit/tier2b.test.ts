/**
 * Tier 2b surfaces: notification-service (inbox/preferences/rules/webhooks/
 * templates/admin ops), tool-plane registry admin (catalog/lifecycle/
 * enablement/BYO queue) and agent-runtime catalog/registry (definitions/
 * versions/tenant config/run history). Response shapes mirror the real
 * downstream route bodies — see services/notification-service/internal/api/,
 * services/tool-plane/internal/api/ and
 * services/agent-runtime/app/api/routes/{registry,chat}.py.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest, type Handler } from "../helpers/mockFetch.js";

const cfg = testConfig();
const ADMIN_CLAIMS = { sub: "u-1", tenant_id: "t-42", typ: "user", scopes: ["*"], workspace_id: "ws-1" };

async function run(handler: Handler, query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = mockFetch(handler);
  const ctx = await makeTestContext(fetchImpl, ADMIN_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

const notFound = { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };

// ============================================================================
// notification-service
// ============================================================================
const WEBHOOK = {
  id: "wh-1", tenant_id: "t-42", url: "https://hooks.example.com/x",
  event_types: ["case.assigned.v1"], active: true,
  secrets: [{ version: 1, secret: "s3cr3t", created_at: "2026-07-12T00:00:00Z" }],
  verified_at: "2026-07-12T00:00:00Z", circuit_state: "closed", consecutive_failures: 0,
  created_by: "u-1", created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
};

function notificationHandler(req: CapturedRequest) {
  if (req.path === "/api/v1/notifications" && req.method === "GET") {
    return {
      body: {
        data: [{
          id: "n-1", event_type: "case.assigned.v1", severity_class: "action",
          title: "Case assigned to you", body: "Case #12", resource_urn: "wr:t-42:case:case/c-1",
          deep_link: "/cases/c-1", read_at: null, created_at: "2026-07-12T01:00:00Z",
        }],
        page: { next_cursor: "n-1", has_more: true },
      },
    };
  }
  if (req.path === "/api/v1/notifications/unread-count") return { body: { data: { unread: 3 } } };
  if (req.path === "/api/v1/notifications/n-1/read" && req.method === "POST") return { status: 204 };
  if (req.path === "/api/v1/notifications/mark-all-read" && req.method === "POST") {
    return { body: { data: { marked: 3 } } };
  }
  if (req.path === "/api/v1/preferences" && req.method === "GET") {
    return { body: { data: { channel_overrides: { "case.assigned.v1": ["email"] }, mutes: {}, quiet_hours: null, digest_config: {}, updated_at: "2026-07-12T00:00:00Z" } } };
  }
  if (req.path === "/api/v1/preferences" && req.method === "PUT") {
    return { body: { data: { channel_overrides: req.body.channel_overrides ?? {}, mutes: req.body.mutes ?? {}, quiet_hours: req.body.quiet_hours ?? null, digest_config: req.body.digest_config ?? {}, updated_at: "2026-07-12T02:00:00Z" } } };
  }
  if (req.path === "/api/v1/rules" && req.method === "GET") {
    return { body: { data: [{ id: "r-1", scope: "user", subject_type: "user", subject_id: "u-1", event_types: ["case.assigned.v1"], channels: ["inapp"], digest_enabled: false, digest_window: "1h", active: true, created_by: "u-1", created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" }], page: { has_more: false } } };
  }
  if (req.path === "/api/v1/rules" && req.method === "POST") {
    return { status: 201, body: { data: { id: "r-new", scope: req.body.scope ?? "user", subject_type: "user", subject_id: "u-1", event_types: req.body.event_types, channels: req.body.channels, digest_enabled: false, digest_window: "1h", active: true, created_by: "u-1", created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z" } } };
  }
  if (req.path === "/api/v1/rules/r-new" && req.method === "DELETE") return { status: 204 };
  if (req.path === "/api/v1/webhooks" && req.method === "GET") {
    return { body: { data: [WEBHOOK], page: { has_more: false } } };
  }
  if (req.path === "/api/v1/webhooks" && req.method === "POST") {
    return { status: 201, body: { data: { ...WEBHOOK, id: "wh-new", url: req.body.url, event_types: req.body.event_types } } };
  }
  if (req.path === "/api/v1/webhooks/wh-1/rotate-secret" && req.method === "POST") {
    return { body: { data: { ...WEBHOOK, secrets: [
      { version: 1, secret: "s3cr3t", created_at: "2026-07-12T00:00:00Z", expires_at: "2026-07-13T00:00:00Z" },
      { version: 2, secret: "n3w-s3cr3t", created_at: "2026-07-12T02:00:00Z" },
    ] } } };
  }
  if (req.path === "/api/v1/webhooks/wh-1" && req.method === "DELETE") return { status: 204 };
  if (req.path === "/api/v1/webhooks/wh-1/deliveries" && req.method === "GET") {
    return { body: { data: [{ id: "d-1", event_id: "e-1", status: "failed", attempts: 3, last_error: "500", created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:10:00Z" }], page: { has_more: false } } };
  }
  if (req.path === "/api/v1/webhooks/wh-1/deliveries/d-1/redeliver" && req.method === "POST") {
    return { status: 202, body: { data: { status: "requeued" } } };
  }
  if (req.path === "/api/v1/templates" && req.method === "GET") {
    return { body: { data: [{ id: "tpl-1", key: "case.assigned.v1", channel: "email", locale: "en", version: 2, subject_tpl: "Case {{.CaseNumber}}", body_html_tpl: "<b>hi</b>", body_text_tpl: "hi", status: "published", published_at: "2026-07-12T00:00:00Z", created_by: "u-1", created_at: "2026-07-11T00:00:00Z" }] } };
  }
  if (req.path === "/api/v1/templates/case.assigned.v1/preview" && req.method === "POST") {
    return { body: { data: { subject: "Case 12", html: "<b>hi</b>", text: "hi" } } };
  }
  if (req.path === "/api/v1/admin/stats") {
    return { body: { data: { window: "24h0m0s", by_channel: { email: { sent: 5 } } } } };
  }
  if (req.path === "/api/v1/admin/suppressions" && req.method === "GET") {
    return { body: { data: [{ id: "s-1", email_hash: "abc", reason: "bounce", created_at: "2026-07-12T00:00:00Z" }] } };
  }
  if (req.path === "/api/v1/admin/suppressions" && req.method === "DELETE") return { status: 204 };
  return notFound;
}

describe("Tier 2b: notification-service", () => {
  it("lists the inbox with unread filter + pagination vars", async () => {
    const { body, requests } = await run(
      notificationHandler,
      `{ notifications(unread: true, first: 10) { nodes { id title eventType readAt deepLink } pageInfo { hasMore nextCursor } } }`,
    );
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).notifications;
    expect(conn.nodes[0]).toMatchObject({ id: "n-1", title: "Case assigned to you", eventType: "case.assigned.v1", readAt: null });
    expect(conn.pageInfo).toMatchObject({ hasMore: true, nextCursor: "n-1" });
    expect(requests[0]!.search.get("filter[unread]")).toBe("true");
    expect(requests[0]!.search.get("limit")).toBe("10");
    expect(requests[0]!.headers.authorization).toMatch(/^Bearer /);
  });

  it("returns the unread count and marks read / all read", async () => {
    const { body } = await run(notificationHandler, `{ notificationUnreadCount }`);
    expect((body?.data as any).notificationUnreadCount).toBe(3);

    const m = await run(notificationHandler, `mutation { markNotificationRead(id: "n-1") }`);
    expect(m.body?.errors).toBeUndefined();
    expect((m.body?.data as any).markNotificationRead).toBe(true);

    const all = await run(notificationHandler, `mutation { markAllNotificationsRead }`);
    expect((all.body?.data as any).markAllNotificationsRead).toBe(3);
  });

  it("reads and replaces preferences", async () => {
    const { body } = await run(notificationHandler, `{ notificationPreferences { channelOverrides digestConfig updatedAt } }`);
    expect((body?.data as any).notificationPreferences.channelOverrides).toEqual({ "case.assigned.v1": ["email"] });

    const m = await run(
      notificationHandler,
      `mutation($input: NotificationPreferencesInput!) { updateNotificationPreferences(input: $input) { channelOverrides updatedAt } }`,
      { input: { channelOverrides: { "case.assigned.v1": ["inapp"] } } },
    );
    expect(m.body?.errors).toBeUndefined();
    expect((m.body?.data as any).updateNotificationPreferences.channelOverrides).toEqual({ "case.assigned.v1": ["inapp"] });
    const put = m.requests.find((r) => r.method === "PUT");
    expect(put?.body.channel_overrides).toEqual({ "case.assigned.v1": ["inapp"] });
  });

  it("rule CRUD passes the snake_case body through", async () => {
    const m = await run(
      notificationHandler,
      `mutation($input: NotificationRuleInput!) { createNotificationRule(input: $input) { id eventTypes channels active } }`,
      { input: { eventTypes: ["case.assigned.v1"], channels: ["inapp", "email"] } },
    );
    expect(m.body?.errors).toBeUndefined();
    expect((m.body?.data as any).createNotificationRule).toMatchObject({ id: "r-new", channels: ["inapp", "email"] });
    expect(m.requests[0]!.body).toMatchObject({ event_types: ["case.assigned.v1"], channels: ["inapp", "email"] });

    const del = await run(notificationHandler, `mutation { deleteNotificationRule(id: "r-new") }`);
    expect((del.body?.data as any).deleteNotificationRule).toBe(true);
  });

  it("webhook create -> list -> rotate -> deliveries -> redeliver -> delete", async () => {
    const created = await run(
      notificationHandler,
      `mutation($input: CreateWebhookInput!) { createNotificationWebhook(input: $input) { id url secrets { version secret } } }`,
      { input: { url: "https://hooks.example.com/x", eventTypes: ["case.assigned.v1"] } },
    );
    expect(created.body?.errors).toBeUndefined();
    // The signing secret IS surfaced on create (shown-once UX in the UI).
    expect((created.body?.data as any).createNotificationWebhook.secrets[0].secret).toBe("s3cr3t");

    const list = await run(notificationHandler, `{ notificationWebhooks { nodes { id url active circuitState } } }`);
    expect((list.body?.data as any).notificationWebhooks.nodes[0]).toMatchObject({ id: "wh-1", active: true, circuitState: "closed" });

    const rotated = await run(notificationHandler, `mutation { rotateNotificationWebhookSecret(id: "wh-1") { secrets { version secret expiresAt } } }`);
    const secrets = (rotated.body?.data as any).rotateNotificationWebhookSecret.secrets;
    expect(secrets).toHaveLength(2);
    expect(secrets[1]).toMatchObject({ version: 2, secret: "n3w-s3cr3t" });

    const deliveries = await run(notificationHandler, `{ notificationWebhookDeliveries(webhookId: "wh-1") { nodes { id status attempts lastError } } }`);
    expect((deliveries.body?.data as any).notificationWebhookDeliveries.nodes[0]).toMatchObject({ id: "d-1", status: "failed", attempts: 3 });

    const redelivered = await run(notificationHandler, `mutation { redeliverNotificationWebhookDelivery(webhookId: "wh-1", deliveryId: "d-1") }`);
    expect((redelivered.body?.data as any).redeliverNotificationWebhookDelivery).toBe(true);

    const deleted = await run(notificationHandler, `mutation { deleteNotificationWebhook(id: "wh-1") }`);
    expect((deleted.body?.data as any).deleteNotificationWebhook).toBe(true);
  });

  it("lists templates by key, previews, and reads admin stats + suppressions", async () => {
    const t = await run(notificationHandler, `{ notificationTemplates(key: "case.assigned.v1") { id key channel version status } }`);
    expect((t.body?.data as any).notificationTemplates[0]).toMatchObject({ id: "tpl-1", key: "case.assigned.v1", version: 2, status: "published" });
    expect(t.requests[0]!.search.get("filter[key]")).toBe("case.assigned.v1");

    const p = await run(notificationHandler, `mutation { previewNotificationTemplate(key: "case.assigned.v1", channel: "email") { subject html text } }`);
    expect((p.body?.data as any).previewNotificationTemplate).toEqual({ subject: "Case 12", html: "<b>hi</b>", text: "hi" });

    const s = await run(notificationHandler, `{ notificationDeliveryStats(window: "24h") { window byChannel } emailSuppressions { id emailHash reason } }`);
    expect((s.body?.data as any).notificationDeliveryStats.window).toBe("24h0m0s");
    expect((s.body?.data as any).emailSuppressions[0]).toMatchObject({ emailHash: "abc", reason: "bounce" });

    const c = await run(notificationHandler, `mutation { clearEmailSuppression(emailHash: "abc") }`);
    expect((c.body?.data as any).clearEmailSuppression).toBe(true);
    const delReq = c.requests.find((r) => r.method === "DELETE");
    expect(delReq?.search.get("email_hash")).toBe("abc");
  });
});

// ============================================================================
// tool-plane registry admin
// ============================================================================
const TOOL = {
  tool_id: "case.assign", display_name: "Assign case", owner_service: "case-service",
  owner_team: "claims", enabled_by_default: true, side_effects: "reversible",
  tags: ["case"], created_at: "2026-07-12T00:00:00Z", updated_at: "2026-07-12T00:00:00Z",
};

function toolPlaneHandler(req: CapturedRequest) {
  if (req.path === "/api/v1/tools" && req.method === "GET") {
    return { body: { data: [TOOL], page: { has_more: false } } };
  }
  if (req.path === "/api/v1/tools" && req.method === "POST") {
    return { status: 201, body: { data: { ...TOOL, tool_id: req.body.tool_id, owner_service: req.body.owner_service } } };
  }
  if (req.path === "/api/v1/tools/case.assign/versions" && req.method === "POST") {
    return { status: 201, body: { data: { tool_id: "case.assign", version: req.body.version, status: "draft", semantic_description: req.body.semantic_description, permission_tier: req.body.permission_tier, cost_weight: req.body.cost_weight } } };
  }
  if (req.path === "/api/v1/tools/case.assign/versions/1.0.0/publish" && req.method === "POST") {
    return { body: { data: { tool_id: "case.assign", version: "1.0.0", status: "published", permission_tier: "write-proposal" } } };
  }
  if (req.path === "/api/v1/tools/case.assign/versions/1.0.0/deprecate" && req.method === "POST") {
    return { body: { data: { status: "deprecated", deprecation_ends_at: "2026-10-12T00:00:00Z" } } };
  }
  if (req.path === "/api/v1/tools/case.assign/versions/1.0.0/retire" && req.method === "POST") {
    return { body: { data: { status: "retired" } } };
  }
  if (req.path === "/api/v1/tools/case.assign/health") {
    return { body: { data: { tool_id: "case.assign", versions: [{ version: "1.0.0", status: "published", declared_sla: { p95_ms: 800 }, health: { p95_ms: 120, error_rate_pct: 0.5 } }] } } };
  }
  if (req.path === "/api/v1/tools/case.assign/schema") {
    return { body: { data: { tool_id: "case.assign", version: "1.0.0", input_schema: { type: "object" }, output_schema: { type: "object" } } } };
  }
  if (req.path === "/api/v1/tenants/self/tools/case.assign" && req.method === "PUT") {
    return { body: { data: { tenant_id: "t-42", tool_id: "case.assign", enabled: req.body.enabled, max_tier_override: req.body.max_tier_override ?? "", updated_at: "2026-07-12T03:00:00Z" } } };
  }
  if (req.path === "/api/v1/byo" && req.method === "GET") {
    return { body: { data: [{ id: "byo-1", manifest: { name: "ext" }, endpoint_url: "https://ext.example.com", auth_method: "api_key", requested_tier: "read", status: "pending_approval", created_at: "2026-07-12T00:00:00Z" }] } };
  }
  if (req.path === "/api/v1/byo" && req.method === "POST") {
    return { status: 201, body: { data: { id: "byo-new", manifest: req.body.manifest, endpoint_url: req.body.endpoint_url, auth_method: "api_key", requested_tier: req.body.requested_tier, status: "pending_approval" } } };
  }
  if (req.path === "/api/v1/byo/byo-1/approve" && req.method === "POST") {
    return { body: { data: { id: "byo-1", status: "approved", decided_by: "u-1" } } };
  }
  return notFound;
}

describe("Tier 2b: tool-plane registry admin", () => {
  it("lists the catalog and registers a tool", async () => {
    const list = await run(toolPlaneHandler, `{ tools { nodes { toolId displayName ownerService sideEffects enabledByDefault } pageInfo { hasMore } } }`);
    expect(list.body?.errors).toBeUndefined();
    expect((list.body?.data as any).tools.nodes[0]).toMatchObject({ toolId: "case.assign", ownerService: "case-service", sideEffects: "reversible" });

    const reg = await run(
      toolPlaneHandler,
      `mutation($input: RegisterToolInput!) { registerTool(input: $input) { toolId ownerService } }`,
      { input: { toolId: "case.assign", ownerService: "case-service" } },
    );
    expect(reg.body?.errors).toBeUndefined();
    expect(reg.requests[0]!.body).toMatchObject({ tool_id: "case.assign", owner_service: "case-service" });
  });

  it("runs the version lifecycle: add draft -> publish -> deprecate -> retire", async () => {
    const add = await run(
      toolPlaneHandler,
      `mutation($input: AddToolVersionInput!) { addToolVersion(toolId: "case.assign", input: $input) { version status } }`,
      { input: { version: "1.0.0", semanticDescription: "Assigns a claims case to an adjuster. Use when routing new cases.", permissionTier: "write-proposal", costWeight: 2 } },
    );
    expect(add.body?.errors).toBeUndefined();
    expect((add.body?.data as any).addToolVersion).toMatchObject({ version: "1.0.0", status: "draft" });
    expect(add.requests[0]!.body.semantic_description).toContain("Use when");

    const pub = await run(toolPlaneHandler, `mutation { publishToolVersion(toolId: "case.assign", version: "1.0.0") { status } }`);
    expect((pub.body?.data as any).publishToolVersion.status).toBe("published");

    const dep = await run(toolPlaneHandler, `mutation { deprecateToolVersion(toolId: "case.assign", version: "1.0.0") { status deprecationEndsAt } }`);
    expect((dep.body?.data as any).deprecateToolVersion.status).toBe("deprecated");

    const ret = await run(toolPlaneHandler, `mutation { retireToolVersion(toolId: "case.assign", version: "1.0.0", force: true, reason: "cleanup") { status } }`);
    expect((ret.body?.data as any).retireToolVersion.status).toBe("retired");
    const retireReq = ret.requests[0];
    expect(retireReq!.body).toMatchObject({ force: true, reason: "cleanup" });
  });

  it("reads health + schema", async () => {
    const { body } = await run(toolPlaneHandler, `{ toolHealth(toolId: "case.assign") { toolId versions { version status declaredSla health } } toolSchema(toolId: "case.assign") { version inputSchema } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).toolHealth.versions[0]).toMatchObject({ version: "1.0.0", status: "published" });
    expect((body?.data as any).toolSchema.version).toBe("1.0.0");
  });

  it("toggles per-tenant enablement (PUT /tenants/self/tools/{id})", async () => {
    const { body, requests } = await run(
      toolPlaneHandler,
      `mutation($input: SetToolEnablementInput!) { setToolEnablement(toolId: "case.assign", input: $input) { toolId enabled maxTierOverride } }`,
      { input: { enabled: false } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).setToolEnablement).toMatchObject({ toolId: "case.assign", enabled: false });
    expect(requests[0]!.method).toBe("PUT");
    expect(requests[0]!.body).toMatchObject({ enabled: false });
  });

  it("lists and decides the BYO queue", async () => {
    const list = await run(toolPlaneHandler, `{ byoSubmissions(status: "pending_approval") { id endpointUrl requestedTier status } }`);
    expect(list.body?.errors).toBeUndefined();
    expect((list.body?.data as any).byoSubmissions[0]).toMatchObject({ id: "byo-1", status: "pending_approval" });
    expect(list.requests[0]!.search.get("filter[status]")).toBe("pending_approval");

    const dec = await run(toolPlaneHandler, `mutation { approveByoTool(id: "byo-1", message: "ok") { id status decidedBy } }`);
    expect((dec.body?.data as any).approveByoTool).toMatchObject({ id: "byo-1", status: "approved", decidedBy: "u-1" });
  });
});

// ============================================================================
// agent-runtime catalog/registry
// ============================================================================
function agentHandler(req: CapturedRequest) {
  if (req.path === "/api/v1/registry/agents" && req.method === "GET") {
    return { body: { data: [{ agent_key: "case-triage", display_name: "Case Triage", description: "Triage assistant", owner_team: "platform-ai", default_write_mode: "proposal", status: "published", latest_published_version: 1 }] } };
  }
  if (req.path === "/api/v1/registry/agents/case-triage/versions" && req.method === "GET") {
    return { body: { data: [{ agent_key: "case-triage", version: 1, status: "published", graph_ref: "graphs/triage.py", graph_digest: "sha256:abc", guardrail_profile: "standard", eval_gate_result_id: "eval-1", toolset: [], model_config: {} }] } };
  }
  if (req.path === "/api/v1/registry/agents/case-triage/versions/2/publish" && req.method === "POST") {
    return { body: { data: { agent_key: "case-triage", version: 2, status: "published" } } };
  }
  if (req.path === "/api/v1/registry/tenants/self/agents/case-triage" && req.method === "GET") {
    return { body: { data: { agent_key: "case-triage", configured: true, enabled: false, pinned_version: 1, prompt_params: {}, auto_execute_policy: {}, self_approval: false } } };
  }
  if (req.path === "/api/v1/registry/tenants/self/agents/case-triage" && req.method === "PUT") {
    return { body: { data: { agent_key: "case-triage", enabled: req.body.enabled, pinned_version: req.body.pinned_version ?? null } } };
  }
  if (req.path === "/api/v1/runs" && req.method === "GET") {
    return { body: { data: [{ id: "run-1", session_id: "sess-1", agent_key: "case-triage", agent_version: 1, status: "succeeded", principal_type: "user_obo", usage: { input_tokens: 10, output_tokens: 20 }, created_at: "2026-07-12T01:00:00Z" }], page: { next_cursor: null, has_more: false } } };
  }
  return notFound;
}

describe("Tier 2b: agent-runtime catalog/registry", () => {
  it("browses definitions and versions", async () => {
    const { body } = await run(agentHandler, `{ agentDefinitions { agentKey displayName status latestPublishedVersion } agentVersions(agentKey: "case-triage") { version status evalGateResultId } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).agentDefinitions[0]).toMatchObject({ agentKey: "case-triage", latestPublishedVersion: 1 });
    expect((body?.data as any).agentVersions[0]).toMatchObject({ version: 1, status: "published", evalGateResultId: "eval-1" });
  });

  it("publishes a version (force + reason pass through)", async () => {
    const { body, requests } = await run(
      agentHandler,
      `mutation { publishAgentVersion(agentKey: "case-triage", version: 2, force: true, reason: "hotfix") { agentKey version status } }`,
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).publishAgentVersion).toMatchObject({ agentKey: "case-triage", version: 2, status: "published" });
    expect(requests[0]!.body).toMatchObject({ force: true, reason: "hotfix" });
  });

  it("reads + writes tenant agent config (PUT then re-read for the full row)", async () => {
    const read = await run(agentHandler, `{ tenantAgentConfig(agentKey: "case-triage") { agentKey configured enabled pinnedVersion selfApproval } }`);
    expect((read.body?.data as any).tenantAgentConfig).toMatchObject({ configured: true, enabled: false, pinnedVersion: 1 });

    const put = await run(
      agentHandler,
      `mutation($input: TenantAgentConfigInput!) { putTenantAgentConfig(agentKey: "case-triage", input: $input) { agentKey configured enabled } }`,
      { input: { enabled: false } },
    );
    expect(put.body?.errors).toBeUndefined();
    // PUT then GET: the mutation result is the re-read full config row.
    expect(put.requests.map((r) => r.method)).toEqual(["PUT", "GET"]);
    expect((put.body?.data as any).putTenantAgentConfig).toMatchObject({ configured: true, enabled: false });
  });

  it("lists the tenant run history", async () => {
    const { body, requests } = await run(
      agentHandler,
      `{ agentRuns(agentKey: "case-triage", first: 25) { nodes { id agentKey agentVersion status principalType usage createdAt } pageInfo { hasMore } } }`,
    );
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).agentRuns;
    expect(conn.nodes[0]).toMatchObject({ id: "run-1", agentKey: "case-triage", status: "SUCCEEDED", principalType: "user_obo" });
    expect(conn.pageInfo.hasMore).toBe(false);
    const req0 = requests[0]!;
    expect(req0.search.get("filter[agent_key]")).toBe("case-triage");
    expect(req0.search.get("limit")).toBe("25");
  });
});

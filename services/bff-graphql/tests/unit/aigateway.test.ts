/**
 * ai-gateway admin plane: provider/deployment catalog + drain, routing ladders,
 * ai-gateway's OWN LLM-spend budgets (distinct from usage-service's Budget),
 * virtual keys, and guardrail policy. Response shapes mirror the real downstream
 * route bodies — see services/ai-gateway/app/api/routes/admin.py.
 */
import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

function aigw() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/admin/providers" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            {
              id: "dep-1", provider: "anthropic", model_family: "claude-judge", deployment_name: "claude-prod",
              region: "us-east-1", cloud: "aws", endpoint_vault_ref: "vault://aigw/dep-1", tpm_limit: 100000,
              rpm_limit: 500, priority: 10, status: "active", created_at: "2026-07-01T00:00:00Z",
              circuit_state: "closed", healthy: true,
            },
          ],
          page: { next_cursor: null, has_more: false },
        },
      };
    }
    if (req.path === "/api/v1/admin/providers" && req.method === "POST") {
      expect(req.body).toMatchObject({ provider: "ollama", deployment_name: "ollama-local" });
      return {
        status: 201,
        body: {
          data: {
            id: "dep-2", provider: "ollama", model_family: "llama3", deployment_name: "ollama-local",
            region: "local", cloud: "aws", endpoint_vault_ref: "vault://aigw/dep-2", tpm_limit: 0,
            rpm_limit: 0, priority: 100, status: "active", created_at: "2026-07-12T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/admin/providers/dep-2/drain" && req.method === "POST") {
      return {
        status: 200,
        body: {
          data: {
            id: "dep-2", provider: "ollama", model_family: "llama3", deployment_name: "ollama-local",
            region: "local", cloud: "aws", endpoint_vault_ref: "vault://aigw/dep-2", tpm_limit: 0,
            rpm_limit: 0, priority: 100, status: "draining", created_at: "2026-07-12T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/admin/ladders/chat" && req.method === "GET") {
      return {
        status: 200,
        body: { data: { id: "ld-1", request_class: "chat", scope: "platform", rungs: [{ model_alias: "cheap", max_tokens: 512, temperature_default: 0.2, cost_tier: 1 }], version: 1, max_rung: 2 } },
      };
    }
    if (req.path === "/api/v1/admin/budgets" && req.method === "POST") {
      expect(req.body).toMatchObject({ scope_type: "tenant", window: "monthly" });
      return {
        status: 201,
        body: {
          data: {
            id: "bud-1", scope_type: "tenant", scope_ref: "t-42", window: "monthly", limit_usd: 500,
            degrade_pct: 95, status: "active", created_at: "2026-07-12T00:00:00Z",
          },
        },
      };
    }
    if (req.path === "/api/v1/admin/spend" && req.method === "GET") {
      expect(req.search.get("scope_type")).toBe("tenant");
      return {
        status: 200,
        body: {
          data: [
            { budget_id: "bud-1", scope_type: "tenant", scope_ref: "t-42", window: "monthly", window_start: "2026-07-01", limit_usd: 500, spend_usd: 42.5, reserved_usd: 1.2, reset_at: "2026-08-01T00:00:00Z" },
          ],
        },
      };
    }
    if (req.path === "/api/v1/admin/keys" && req.method === "POST") {
      expect(req.body).toMatchObject({ principal_type: "agent" });
      return {
        status: 201,
        body: {
          data: {
            id: "key-1", principal_type: "agent", principal_id: "claims-agent", allowed_request_classes: ["chat"],
            max_rung: 2, expires_at: null, status: "active", created_at: "2026-07-12T00:00:00Z", secret: "nk-live-abc123",
          },
        },
      };
    }
    if (req.path === "/api/v1/admin/keys/key-1/revoke" && req.method === "POST") {
      return {
        status: 200,
        body: {
          data: { id: "key-1", principal_type: "agent", principal_id: "claims-agent", allowed_request_classes: ["chat"], max_rung: 2, status: "revoked", created_at: "2026-07-12T00:00:00Z" },
        },
      };
    }
    if (req.path === "/api/v1/admin/guardrails" && req.method === "GET") {
      return { status: 200, body: { data: { policy: { pii: { mode: "redact" }, injection: { mode: "block" }, schema_validation: "on" }, version: 3 } } };
    }
    if (req.path === "/api/v1/admin/guardrails" && req.method === "PUT") {
      expect(req.body).toMatchObject({ policy: { pii: { mode: "block" } } });
      return { status: 200, body: { data: { policy: { pii: { mode: "block" } }, version: 4 } } };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

const OPERATOR_CLAIMS = { sub: "op-1", tenant_id: "t-42", typ: "user", scopes: ["*"] };

async function run(query: string, variables: Record<string, unknown> = {}) {
  const server = makeApolloServer(cfg);
  const { fetchImpl, requests } = aigw();
  const ctx = await makeTestContext(fetchImpl, OPERATOR_CLAIMS);
  const res = await server.executeOperation({ query, variables }, { contextValue: ctx });
  const body = res.body.kind === "single" ? res.body.singleResult : null;
  return { body, requests };
}

describe("ai-gateway: provider/deployment catalog", () => {
  it("aiProviders lists the catalog with live circuit/health flags", async () => {
    const { body } = await run(`{ aiProviders { nodes { id provider deploymentName circuitState healthy } } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).aiProviders.nodes[0]).toMatchObject({ id: "dep-1", provider: "anthropic", circuitState: "closed", healthy: true });
  });

  it("createAiProvider registers a new deployment", async () => {
    const { body } = await run(
      `mutation($input: CreateAiProviderInput!){ createAiProvider(input: $input) { id provider status } }`,
      { input: { provider: "ollama", modelFamily: "llama3", deploymentName: "ollama-local", region: "local", cloud: "aws", endpointVaultRef: "vault://aigw/dep-2" } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createAiProvider).toMatchObject({ id: "dep-2", status: "active" });
  });

  it("drainAiProvider POSTs the drain action", async () => {
    const { body, requests } = await run(`mutation{ drainAiProvider(deploymentId: "dep-2") { id status } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).drainAiProvider).toMatchObject({ id: "dep-2", status: "draining" });
    expect(requests.some((r) => r.method === "POST" && r.path === "/api/v1/admin/providers/dep-2/drain")).toBe(true);
  });
});

describe("ai-gateway: routing ladder", () => {
  it("aiLadder reads the chat request-class ladder", async () => {
    const { body } = await run(`{ aiLadder(requestClass: "chat") { requestClass version rungs } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).aiLadder).toMatchObject({ requestClass: "chat", version: 1 });
  });
});

describe("ai-gateway: own LLM-spend budgets + live spend", () => {
  it("createAiBudget creates a budget distinct from usage-service's Budget type", async () => {
    const { body } = await run(
      `mutation($input: CreateAiBudgetInput!){ createAiBudget(input: $input) { id scopeType window limitUsd } }`,
      { input: { scopeType: "tenant", scopeRef: "t-42", window: "monthly", limitUsd: 500 } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createAiBudget).toMatchObject({ id: "bud-1", scopeType: "tenant", limitUsd: 500 });
  });

  it("aiSpend returns live spend rows for a scope", async () => {
    const { body } = await run(`{ aiSpend(scopeType: "tenant", scopeRef: "t-42") { budgetId spendUsd reservedUsd } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).aiSpend[0]).toMatchObject({ budgetId: "bud-1", spendUsd: 42.5 });
  });
});

describe("ai-gateway: virtual keys", () => {
  it("createAiVirtualKey returns the secret ONCE", async () => {
    const { body } = await run(
      `mutation($input: CreateAiVirtualKeyInput!){ createAiVirtualKey(input: $input) { id status secret } }`,
      { input: { principalType: "agent", principalId: "claims-agent", allowedRequestClasses: ["chat"] } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createAiVirtualKey).toMatchObject({ id: "key-1", secret: "nk-live-abc123" });
  });

  it("revokeAiVirtualKey never carries a secret", async () => {
    const { body } = await run(`mutation{ revokeAiVirtualKey(id: "key-1") { id status secret } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).revokeAiVirtualKey).toMatchObject({ id: "key-1", status: "revoked", secret: null });
  });
});

describe("ai-gateway: guardrail policy", () => {
  it("aiGuardrailPolicy reads the current policy", async () => {
    const { body } = await run(`{ aiGuardrailPolicy { version policy } }`);
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).aiGuardrailPolicy).toMatchObject({ version: 3 });
  });

  it("putAiGuardrailPolicy replaces the policy (bumps version)", async () => {
    const { body } = await run(
      `mutation($policy: JSON!){ putAiGuardrailPolicy(policy: $policy) { version } }`,
      { policy: { pii: { mode: "block" } } },
    );
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).putAiGuardrailPolicy).toMatchObject({ version: 4 });
  });
});

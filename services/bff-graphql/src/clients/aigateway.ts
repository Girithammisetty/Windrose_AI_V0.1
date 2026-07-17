/**
 * ai-gateway admin-plane REST client (AIG-FR-070). Backs: the provider/deployment
 * catalog, model-routing ladders, ai-gateway's OWN LLM-spend budgets + live spend
 * (a distinct concept from usage-service's platform-cost Budget — see usage.ts),
 * virtual API keys (scoped credentials agents use to call the gateway), and the
 * guardrail policy (PII/injection/output-schema rules). Cache invalidation
 * (`DELETE /admin/cache`) is ops-only and intentionally not wired here.
 */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

/** ai-gateway ProviderDeployment (+ live circuit/health flags admin.py attaches). */
export interface AiProviderDTO {
  id: string;
  provider: string;
  model_family: string;
  deployment_name: string;
  region: string;
  cloud: string;
  endpoint_vault_ref: string;
  tpm_limit: number;
  rpm_limit: number;
  priority: number;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
  circuit_state?: string;
  healthy?: boolean;
}

/** ai-gateway ModelLadder (per request-class routing rungs). */
export interface AiLadderDTO {
  id: string;
  request_class: string;
  scope: string;
  rungs: Record<string, unknown>[];
  version: number;
  max_rung?: number | null;
}

/** ai-gateway's own Budget (LLM spend, distinct from usage-service Budget). */
export interface AiBudgetDTO {
  id: string;
  scope_type: string;
  scope_ref: string;
  window: string;
  limit_usd: number;
  degrade_pct: number;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

/** ai-gateway live-spend row (BudgetEngine.live_spend). */
export interface AiSpendRowDTO {
  budget_id: string;
  scope_type: string;
  scope_ref: string;
  window: string;
  window_start: string;
  limit_usd: number;
  spend_usd: number;
  reserved_usd: number;
  reset_at: string;
}

/** ai-gateway VirtualKey. `secret` is present only on the create/rotate response
 * (shown once, AIG-FR-030) — absent on list/read. */
export interface AiVirtualKeyDTO {
  id: string;
  principal_type: string;
  principal_id: string;
  allowed_request_classes: string[];
  max_rung: number;
  expires_at?: string | null;
  status: string;
  created_at?: string | null;
  secret?: string;
}

export interface AiGuardrailPolicyDTO {
  policy: Record<string, unknown>;
  version: number;
}

// ai-gateway cost-detail breakdown (GET /admin/spend/breakdown) — REAL
// aggregation from the request_log, rolled up per provider / per (provider,
// model) / per request-class over a window. Added this session for the
// provider-agnostic + cost-detailed work.
export interface AiCostRollupDTO {
  provider?: string | null;
  model?: string | null;
  model_alias?: string;
  request_class?: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface AiCostBreakdownDTO {
  window: { since: string; hours: number; price_version: string };
  totals: { requests: number; input_tokens: number; output_tokens: number; cost_usd: number };
  by_provider: AiCostRollupDTO[];
  by_model: AiCostRollupDTO[];
  by_request_class: AiCostRollupDTO[];
  detail: AiCostRollupDTO[];
}

export interface CreateAiProviderBody {
  provider: string;
  model_family: string;
  deployment_name: string;
  region: string;
  cloud: string;
  endpoint_vault_ref: string;
  tpm_limit?: number;
  rpm_limit?: number;
  priority?: number;
}

export interface PatchAiProviderBody {
  status?: string;
  priority?: number;
  tpm_limit?: number;
  rpm_limit?: number;
  endpoint_vault_ref?: string;
  reason?: string;
}

export interface CreateAiBudgetBody {
  scope_type: string;
  scope_ref: string;
  window: string;
  limit_usd: number;
  degrade_pct?: number;
}

export interface PatchAiBudgetBody {
  limit_usd?: number;
  degrade_pct?: number;
  status?: string;
}

export interface CreateAiVirtualKeyBody {
  principal_type: string;
  principal_id: string;
  allowed_request_classes?: string[];
  max_rung?: number;
  ttl_seconds?: number;
}

export class AiGatewayClient {
  constructor(private readonly http: ServiceClient) {}

  // ---- provider/deployment catalog ---------------------------------------
  async providers(limit: number, cursor?: string): Promise<Page<AiProviderDTO>> {
    return this.http.get<Page<AiProviderDTO>>("/api/v1/admin/providers", { query: { limit, cursor } });
  }

  async createProvider(body: CreateAiProviderBody, idempotencyKey?: string): Promise<AiProviderDTO> {
    const r = await this.http.post<{ data: AiProviderDTO }>("/api/v1/admin/providers", { body, idempotencyKey });
    return unwrap<AiProviderDTO>(r);
  }

  async patchProvider(deploymentId: string, body: PatchAiProviderBody, force = false): Promise<AiProviderDTO> {
    const r = await this.http.patch<{ data: AiProviderDTO }>(
      `/api/v1/admin/providers/${encodeURIComponent(deploymentId)}`,
      { body, query: { force } },
    );
    return unwrap<AiProviderDTO>(r);
  }

  async drainProvider(deploymentId: string, force = false): Promise<AiProviderDTO> {
    const r = await this.http.post<{ data: AiProviderDTO }>(
      `/api/v1/admin/providers/${encodeURIComponent(deploymentId)}/drain`,
      { query: { force } },
    );
    return unwrap<AiProviderDTO>(r);
  }

  // ---- model routing ladders ----------------------------------------------
  async ladder(requestClass: string): Promise<AiLadderDTO> {
    const r = await this.http.get<{ data: AiLadderDTO }>(
      `/api/v1/admin/ladders/${encodeURIComponent(requestClass)}`,
    );
    return unwrap<AiLadderDTO>(r);
  }

  async putLadder(
    requestClass: string,
    body: { rungs: Record<string, unknown>[]; max_rung?: number; scope?: string },
  ): Promise<AiLadderDTO> {
    const r = await this.http.put<{ data: AiLadderDTO }>(
      `/api/v1/admin/ladders/${encodeURIComponent(requestClass)}`,
      { body },
    );
    return unwrap<AiLadderDTO>(r);
  }

  // ---- ai-gateway's own budgets + live spend -------------------------------
  async budgets(limit: number, cursor?: string, scopeType?: string): Promise<Page<AiBudgetDTO>> {
    return this.http.get<Page<AiBudgetDTO>>("/api/v1/admin/budgets", {
      query: { limit, cursor, "filter[scope_type]": scopeType },
    });
  }

  async budget(id: string): Promise<AiBudgetDTO> {
    const r = await this.http.get<{ data: AiBudgetDTO }>(`/api/v1/admin/budgets/${encodeURIComponent(id)}`);
    return unwrap<AiBudgetDTO>(r);
  }

  async createBudget(body: CreateAiBudgetBody, idempotencyKey?: string): Promise<AiBudgetDTO> {
    const r = await this.http.post<{ data: AiBudgetDTO }>("/api/v1/admin/budgets", { body, idempotencyKey });
    return unwrap<AiBudgetDTO>(r);
  }

  async patchBudget(id: string, body: PatchAiBudgetBody): Promise<AiBudgetDTO> {
    const r = await this.http.patch<{ data: AiBudgetDTO }>(`/api/v1/admin/budgets/${encodeURIComponent(id)}`, {
      body,
    });
    return unwrap<AiBudgetDTO>(r);
  }

  async deleteBudget(id: string): Promise<AiBudgetDTO> {
    const r = await this.http.delete<{ data: AiBudgetDTO }>(`/api/v1/admin/budgets/${encodeURIComponent(id)}`);
    return unwrap<AiBudgetDTO>(r);
  }

  async spend(scopeType: string, scopeRef: string, window?: string): Promise<AiSpendRowDTO[]> {
    const r = await this.http.get<{ data: AiSpendRowDTO[] }>("/api/v1/admin/spend", {
      query: { scope_type: scopeType, scope_ref: scopeRef, window },
    });
    return r.data ?? [];
  }

  // Cost-detail breakdown by provider / model / request-class over a window.
  async costBreakdown(windowHours?: number): Promise<AiCostBreakdownDTO> {
    const r = await this.http.get<{ data: AiCostBreakdownDTO }>("/api/v1/admin/spend/breakdown", {
      query: { window_hours: windowHours },
    });
    return unwrap<AiCostBreakdownDTO>(r);
  }

  // ---- virtual keys ---------------------------------------------------------
  async keys(limit: number, cursor?: string): Promise<Page<AiVirtualKeyDTO>> {
    return this.http.get<Page<AiVirtualKeyDTO>>("/api/v1/admin/keys", { query: { limit, cursor } });
  }

  async createKey(body: CreateAiVirtualKeyBody, idempotencyKey?: string): Promise<AiVirtualKeyDTO> {
    const r = await this.http.post<{ data: AiVirtualKeyDTO }>("/api/v1/admin/keys", { body, idempotencyKey });
    return unwrap<AiVirtualKeyDTO>(r);
  }

  async revokeKey(id: string): Promise<AiVirtualKeyDTO> {
    const r = await this.http.post<{ data: AiVirtualKeyDTO }>(
      `/api/v1/admin/keys/${encodeURIComponent(id)}/revoke`,
    );
    return unwrap<AiVirtualKeyDTO>(r);
  }

  async rotateKey(id: string): Promise<AiVirtualKeyDTO> {
    const r = await this.http.post<{ data: AiVirtualKeyDTO }>(
      `/api/v1/admin/keys/${encodeURIComponent(id)}/rotate`,
    );
    return unwrap<AiVirtualKeyDTO>(r);
  }

  // ---- guardrail policy -----------------------------------------------------
  async guardrails(): Promise<AiGuardrailPolicyDTO> {
    const r = await this.http.get<{ data: AiGuardrailPolicyDTO }>("/api/v1/admin/guardrails");
    return unwrap<AiGuardrailPolicyDTO>(r);
  }

  async putGuardrails(policy: Record<string, unknown>): Promise<AiGuardrailPolicyDTO> {
    const r = await this.http.put<{ data: AiGuardrailPolicyDTO }>("/api/v1/admin/guardrails", {
      body: { policy },
    });
    return unwrap<AiGuardrailPolicyDTO>(r);
  }
}

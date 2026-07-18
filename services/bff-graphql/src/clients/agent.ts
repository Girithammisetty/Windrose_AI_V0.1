/** agent-runtime REST client (BRD 14). Backs: Proposal, AgentRun, TraceNode.
 * NB: agent-runtime paths already include the /api/v1 prefix. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface ProposalDTO {
  id: string;
  run_id?: string;
  agent_key?: string;
  agent_version?: string;
  /** The proposed tool call: proposal_view serializes `tool_id` (+ tool_version). */
  tool_id?: string;
  tool_version?: string;
  /** Legacy field name, kept for defensiveness during contract transition. */
  tool?: string;
  /** The proposed tool arguments: proposal_view serializes `args`. */
  args?: unknown;
  /** Legacy field name, kept for defensiveness during contract transition. */
  args_diff?: unknown;
  rationale?: string;
  affected_urns?: string[];
  predicted_effect?: string;
  expires_at?: string;
  status?: string; // pending|approved|rejected|edited_approved|responded|expired
  decision?: unknown;
  /** The single resource this proposal targets (being added to proposal_view;
   * honored by filter[resource_urn]). */
  resource_urn?: string;
  created_at?: string;
  /** Tool-plane risk tier: read|write-proposal|write-direct|admin (agent-runtime proposal_view). */
  tier?: string;
  risk_tier?: string;
  /** Side-effect class: none|reversible|destructive. */
  side_effects?: string;
}

export interface AgentRunDTO {
  id: string;
  session_id?: string;
  agent_key?: string;
  agent_version?: string;
  status?: string;
  /** run_view serializes token counts under `usage` {input_tokens, output_tokens,
   * model, deployment} (and cost fields if/when the runtime adds them). */
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    model?: string;
    deployment?: string;
    cost_usd?: number;
    cost?: number;
  };
  /** Legacy field names, kept for defensiveness during contract transition. */
  token_usage?: { input_tokens?: number; output_tokens?: number };
  cost_usd?: number;
  citations?: unknown[];
  error?: unknown;
  created_at?: string;
}

export interface DecideBody {
  action: "approve" | "reject" | "edit_args" | "respond";
  message?: string;
  edited_args?: Record<string, unknown>;
}

/** agent-runtime KillSwitch (ART-FR-073). scope: agent|agent_version|agent_version_tenant. */
export interface AgentKillSwitchDTO {
  kill_id: string;
  scope: string;
  agent_key: string;
  version?: number | null;
  tenant_id?: string | null;
  active: boolean;
  reason: string;
  set_by: string;
  created_at?: string | null;
}

export interface CreateAgentKillBody {
  agent_key: string;
  scope?: string;
  version?: number;
  tenant_id?: string;
  reason: string;
}

// ============================================================================
// Tier 2b: agent catalog/registry browse + per-tenant agent config + run
// history. Shapes mirror app/api/routes/registry.py (_definition_view /
// _version_view / _tenant_config_view) and app/api/schemas.py run_view.
// ============================================================================

export interface AgentDefinitionDTO {
  agent_key: string;
  display_name: string;
  description?: string;
  owner_team?: string;
  default_write_mode?: string;
  status?: string;
  latest_published_version?: number | null;
}

export interface AgentVersionDTO {
  agent_key: string;
  version: number;
  status: string;
  graph_ref?: string;
  graph_digest?: string;
  guardrail_profile?: string;
  eval_gate_result_id?: string | null;
  toolset?: unknown[];
  model_config?: Record<string, unknown>;
}

export interface TenantAgentConfigDTO {
  agent_key: string;
  configured: boolean;
  enabled: boolean;
  pinned_version?: number | null;
  prompt_params?: Record<string, unknown>;
  auto_execute_policy?: Record<string, unknown>;
  self_approval: boolean;
}

export interface PutTenantAgentConfigBody {
  enabled?: boolean;
  pinned_version?: number | null;
  prompt_params?: Record<string, unknown>;
  auto_execute_policy?: Record<string, unknown>;
  self_approval?: boolean;
}

/** BRD 53 inc2b: author a custom agent + its guardrail envelope (inc2). Mirrors
 * app/api/routes/registry.py create_custom_agent — server validates + clamps. */
export interface CreateCustomAgentBody {
  display_name: string;
  persona: string;
  system_prompt?: string;
  allowed_tools: string[];
  propose_tool?: string | null;
  data_scope?: { workspaces?: string[]; dataset_urns?: string[] };
  budget?: { max_tokens_per_session?: number };
  pii?: { block_pii_egress?: boolean; redact?: boolean };
}

export interface CustomAgentResultDTO {
  agent_key: string;
  status: string;
  graph_ref: string;
  allowed_tools: string[];
  persona: string;
  owner_tenant: string;
  guardrail_policy?: Record<string, unknown>;
}

export interface AgentCeilingsDTO {
  max_budget_tokens: number;
  max_tier: string;
  updated_at?: string | null;
  updated_by?: string | null;
}

/** run_view row on the Tier 2b GET /runs list (adds created_at). */
export interface AgentRunListItemDTO {
  id: string;
  session_id?: string;
  agent_key?: string;
  agent_version?: number;
  status?: string;
  principal_type?: string;
  usage?: Record<string, unknown>;
  error?: unknown;
  final_text?: string | null;
  created_at?: string;
}

// ---- BRD 54 inc2: decision-model DTOs (agent-runtime decision_view) --------

export interface DecisionConditionDTO {
  column: string;
  op: string;
  value?: unknown;
}
export interface DecisionOutcomeDTO {
  disposition_code: string;
  severity: string;
}
export interface DecisionRuleDTO {
  when: DecisionConditionDTO[];
  then: DecisionOutcomeDTO | null;
  note?: string;
}
export interface DecisionModelDTO {
  id: string;
  name: string;
  version: number;
  status: string;
  workspace_id?: string | null;
  dataset_urn?: string | null;
  created_by?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  rules: DecisionRuleDTO[];
  default_outcome?: DecisionOutcomeDTO | null;
}
export interface CreateDecisionModelBody {
  name: string;
  workspace_id?: string;
  rules: DecisionRuleDTO[];
  default_outcome?: DecisionOutcomeDTO | null;
}
export interface BatchEvaluateRowDTO {
  case_id: string;
  matched: boolean;
  rule_index: number | null;
  explanation: string;
  outcome: DecisionOutcomeDTO | null;
  proposal_id?: string;
  proposal_status?: string;
  executed?: boolean;
}
export interface BatchEvaluateDTO {
  model_id: string;
  proposed: boolean;
  summary: {
    cases: number;
    matched: number;
    unmatched: number;
    proposals_created: number;
    by_outcome: Record<string, number>;
  };
  results: BatchEvaluateRowDTO[];
}

export class AgentClient {
  constructor(private readonly http: ServiceClient) {}

  proposals(params: {
    status?: string;
    agentKey?: string;
    limit: number;
    cursor?: string;
  }): Promise<Page<ProposalDTO>> {
    return this.http.get<Page<ProposalDTO>>("/api/v1/proposals", {
      query: {
        "filter[status]": params.status,
        "filter[agent_key]": params.agentKey,
        limit: params.limit,
        cursor: params.cursor,
      },
    });
  }

  /** proposalByCaseId / resource loader: GET /proposals?filter[resource_urn]=… */
  async proposalsByResourceUrns(urns: string[]): Promise<ProposalDTO[]> {
    const res = await this.http.get<Page<ProposalDTO>>("/api/v1/proposals", {
      query: { "filter[resource_urn]": urns.join(","), limit: 200 },
    });
    return res.data ?? [];
  }

  async proposal(id: string): Promise<ProposalDTO> {
    const r = await this.http.get<{ data: ProposalDTO } | ProposalDTO>(
      `/api/v1/proposals/${encodeURIComponent(id)}`,
    );
    return unwrap<ProposalDTO>(r);
  }

  /** POST /proposals/{id}/decide — mutation passthrough (idempotent, first-wins). */
  async decide(id: string, body: DecideBody, idempotencyKey?: string): Promise<ProposalDTO> {
    const r = await this.http.post<{ data: ProposalDTO } | ProposalDTO>(
      `/api/v1/proposals/${encodeURIComponent(id)}/decide`,
      { body, idempotencyKey },
    );
    return unwrap<ProposalDTO>(r);
  }

  async run(id: string): Promise<AgentRunDTO> {
    const r = await this.http.get<{ data: AgentRunDTO } | AgentRunDTO>(
      `/api/v1/runs/${encodeURIComponent(id)}`,
    );
    return unwrap<AgentRunDTO>(r);
  }

  /** GET /runs/{id}/trace — tool-call tree for the visualizer. */
  async runTrace(id: string): Promise<unknown> {
    return this.http.get(`/api/v1/runs/${encodeURIComponent(id)}/trace`);
  }

  /** GET /registry/kill-switches — active kills (operator: all tenants; tenant
   * admin: own tenant + global). Needs operator or tenant.admin JWT scope. */
  async killSwitches(): Promise<AgentKillSwitchDTO[]> {
    const r = await this.http.get<{ data: AgentKillSwitchDTO[] }>("/api/v1/registry/kill-switches");
    return r.data ?? [];
  }

  /** POST /registry/kill-switches — set a kill (200; needs operator, or tenant
   * admin for the caller's own tenant scope). Response is thin ({kill_id,
   * active}); the resolver re-reads the list to return the full row. */
  async createKillSwitch(body: CreateAgentKillBody, idempotencyKey?: string): Promise<{ kill_id: string; active: boolean }> {
    const r = await this.http.post<{ data: { kill_id: string; active: boolean } }>(
      "/api/v1/registry/kill-switches",
      { body, idempotencyKey },
    );
    return r.data;
  }

  /** DELETE /registry/kill-switches/{id} — lift a kill (200; authN only). */
  async deleteKillSwitch(killId: string): Promise<{ kill_id: string; active: boolean }> {
    const r = await this.http.delete<{ data: { kill_id: string; active: boolean } }>(
      `/api/v1/registry/kill-switches/${encodeURIComponent(killId)}`,
    );
    return r.data;
  }

  // ---- Tier 2b: agent catalog browse (GET routes; operator/tenant.admin) ----

  async agentDefinitions(): Promise<AgentDefinitionDTO[]> {
    const r = await this.http.get<{ data: AgentDefinitionDTO[] }>("/api/v1/registry/agents");
    return r.data ?? [];
  }

  async agentVersions(agentKey: string): Promise<AgentVersionDTO[]> {
    const r = await this.http.get<{ data: AgentVersionDTO[] }>(
      `/api/v1/registry/agents/${encodeURIComponent(agentKey)}/versions`,
    );
    return r.data ?? [];
  }

  /** POST /registry/agents/{key}/versions/{v}/publish — eval-gate-guarded
   * (operator; force requires a reason). */
  async publishAgentVersion(
    agentKey: string,
    version: number,
    force?: boolean,
    reason?: string,
    idempotencyKey?: string,
  ): Promise<{ agent_key: string; version: number; status: string }> {
    const r = await this.http.post<{ data: { agent_key: string; version: number; status: string } }>(
      `/api/v1/registry/agents/${encodeURIComponent(agentKey)}/versions/${version}/publish`,
      { body: force ? { force, reason } : {}, idempotencyKey },
    );
    return r.data;
  }

  // ---- Tier 2b: per-tenant agent config (tenant.admin) ----------------------

  async tenantAgentConfig(agentKey: string): Promise<TenantAgentConfigDTO> {
    const r = await this.http.get<{ data: TenantAgentConfigDTO }>(
      `/api/v1/registry/tenants/self/agents/${encodeURIComponent(agentKey)}`,
    );
    return r.data;
  }

  /** PUT /registry/tenants/self/agents/{key}. The PUT response is thin
   * ({agent_key, enabled, pinned_version}); the resolver re-reads the config
   * for the full row. */
  async putTenantAgentConfig(
    agentKey: string,
    body: PutTenantAgentConfigBody,
    idempotencyKey?: string,
  ): Promise<{ agent_key: string; enabled: boolean; pinned_version?: number | null }> {
    const r = await this.http.put<{ data: { agent_key: string; enabled: boolean; pinned_version?: number | null } }>(
      `/api/v1/registry/tenants/self/agents/${encodeURIComponent(agentKey)}`,
      { body, idempotencyKey },
    );
    return r.data;
  }

  /** POST /registry/tenants/self/agents — author a tenant CUSTOM agent (BRD 53)
   * as governed configuration + its guardrail envelope (inc2: data_scope,
   * budget, pii). Needs ai.agent.admin; validated + clamped server-side. */
  async createCustomAgent(body: CreateCustomAgentBody): Promise<CustomAgentResultDTO> {
    const r = await this.http.post<{ data: CustomAgentResultDTO }>(
      "/api/v1/registry/tenants/self/agents",
      { body },
    );
    return r.data;
  }

  /** POST /registry/tenants/self/personas/autobind — bind persona copilots for
   * pack roles (BRD 53 inc3, PA-FR-010). Idempotent; needs ai.agent.admin. */
  async autobindPersonaCopilots(
    roles: string[],
    proposeTool?: string | null,
  ): Promise<{ created: { role: string; agent_key: string }[]; skipped: { role: string; agent_key: string }[] }> {
    const r = await this.http.post<{
      data: { created: { role: string; agent_key: string }[]; skipped: { role: string; agent_key: string }[] };
    }>("/api/v1/registry/tenants/self/personas/autobind", {
      body: { roles, propose_tool: proposeTool ?? undefined },
    });
    return r.data;
  }

  /** GET /registry/platform/agent-ceilings — operator-only platform ceilings. */
  async agentCeilings(): Promise<AgentCeilingsDTO> {
    const r = await this.http.get<{ data: AgentCeilingsDTO }>("/api/v1/registry/platform/agent-ceilings");
    return r.data;
  }

  /** PUT /registry/platform/agent-ceilings — set them (operator-only). */
  async setAgentCeilings(maxBudgetTokens: number, maxTier: string): Promise<AgentCeilingsDTO> {
    const r = await this.http.put<{ data: AgentCeilingsDTO }>(
      "/api/v1/registry/platform/agent-ceilings",
      { body: { max_budget_tokens: maxBudgetTokens, max_tier: maxTier } },
    );
    return r.data;
  }

  // ---- Tier 2b: run history (any tenant principal; tenant-scoped by RLS) ----

  agentRuns(params: { agentKey?: string; limit: number }): Promise<Page<AgentRunListItemDTO>> {
    return this.http.get<Page<AgentRunListItemDTO>>("/api/v1/runs", {
      query: { "filter[agent_key]": params.agentKey, limit: params.limit },
    });
  }

  // ---- SLM distillation (BRD 12 M1/M2): transcript corpus + curated SFT ----

  /** List transcript-corpus rows (service caps the page at 200 — callers must
   * treat a full page as "200+", never fabricate a total). */
  transcripts(params: { decided?: boolean; limit: number }): Promise<Page<TranscriptDTO>> {
    return this.http.get<Page<TranscriptDTO>>("/api/v1/transcripts", {
      query: { "filter[decided]": params.decided || undefined, limit: params.limit },
    });
  }

  sftDatasets(params: { agentKey?: string; limit: number }): Promise<Page<SftDatasetDTO>> {
    return this.http.get<Page<SftDatasetDTO>>("/api/v1/sft-datasets", {
      query: { "filter[agent_key]": params.agentKey, limit: params.limit },
    });
  }

  // ---- BRD 54 inc2: governed decision tables (authoring + batch) ------------

  async decisionModels(): Promise<DecisionModelDTO[]> {
    const r = await this.http.get<{ data: DecisionModelDTO[] }>("/api/v1/decision-models");
    return r.data ?? [];
  }

  async decisionModel(id: string): Promise<DecisionModelDTO> {
    const r = await this.http.get<{ data: DecisionModelDTO } | DecisionModelDTO>(
      `/api/v1/decision-models/${encodeURIComponent(id)}`,
    );
    return unwrap<DecisionModelDTO>(r);
  }

  async createDecisionModel(body: CreateDecisionModelBody, idempotencyKey?: string): Promise<DecisionModelDTO> {
    const r = await this.http.post<{ data: DecisionModelDTO } | DecisionModelDTO>(
      "/api/v1/decision-models",
      { body, idempotencyKey },
    );
    return unwrap<DecisionModelDTO>(r);
  }

  /** All versions of one logical table, newest first — the change log. */
  async decisionModelVersions(id: string): Promise<DecisionModelDTO[]> {
    const r = await this.http.get<{ data: DecisionModelDTO[] }>(
      `/api/v1/decision-models/${encodeURIComponent(id)}/versions`,
    );
    return r.data ?? [];
  }

  /** Four-eyes approval of the logic: publish a draft (author ≠ approver). */
  async approveDecisionModel(id: string, idempotencyKey?: string): Promise<DecisionModelDTO> {
    const r = await this.http.post<{ data: DecisionModelDTO } | DecisionModelDTO>(
      `/api/v1/decision-models/${encodeURIComponent(id)}/approve`,
      { idempotencyKey },
    );
    return unwrap<DecisionModelDTO>(r);
  }

  /** Edit = a new draft version (prior version stays immutable). */
  async newDecisionModelVersion(id: string, body: { rules?: DecisionRuleDTO[]; default_outcome?: DecisionOutcomeDTO | null }, idempotencyKey?: string): Promise<DecisionModelDTO> {
    const r = await this.http.post<{ data: DecisionModelDTO } | DecisionModelDTO>(
      `/api/v1/decision-models/${encodeURIComponent(id)}/versions`,
      { body, idempotencyKey },
    );
    return unwrap<DecisionModelDTO>(r);
  }

  /** POST /decision-models/{id}/batch-evaluate — dry-run preview by default;
   * ?propose=true mints one governed four-eyes proposal per matched case. */
  async batchEvaluateDecisionModel(
    id: string,
    body: { workspace_id?: string; case_ids?: string[]; limit?: number },
    propose: boolean,
    idempotencyKey?: string,
  ): Promise<BatchEvaluateDTO> {
    const r = await this.http.post<{ data: BatchEvaluateDTO } | BatchEvaluateDTO>(
      `/api/v1/decision-models/${encodeURIComponent(id)}/batch-evaluate`,
      { body, query: { propose }, idempotencyKey },
    );
    return unwrap<BatchEvaluateDTO>(r);
  }
}

/** One captured agent-run transcript (only the fields the loop stats need). */
export interface TranscriptDTO {
  transcript_id: string;
  agent_key?: string;
  decision?: string | null;
  corrected_output?: unknown;
  created_at?: string | null;
}

/** One curated, versioned SFT dataset (agent-runtime sft_datasets). */
export interface SftDatasetDTO {
  dataset_id: string;
  agent_key?: string;
  version?: number;
  status?: string;
  row_count?: number;
  source_count?: number;
  created_at?: string | null;
}

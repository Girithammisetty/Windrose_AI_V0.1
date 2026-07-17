/** tool-plane (tool-registry) REST client. Backs: tool kill switches (TPL-FR-052,
 * ART/Tier-1 safety control) and, since Tier 2b, the registry admin plane —
 * catalog CRUD/lifecycle, per-tenant enablement, BYO onboarding queue. Pure
 * passthrough — the caller's JWT is forwarded verbatim and tool-plane enforces
 * tool.tool.*, tool.enablement.update, tool.byo.*, tool.kill.*. */
import { ServiceClient } from "./base.js";
import type { Page } from "./types.js";

/** tool-plane KillSwitch (internal/domain.KillSwitch). scope: tool|tool_version|tool_tenant. */
export interface ToolKillSwitchDTO {
  id: string;
  scope: string;
  tool_id: string;
  version?: string;
  tenant_id?: string | null;
  active: boolean;
  reason: string;
  set_by: string;
  created_at?: string;
}

export interface CreateToolKillBody {
  scope: string;
  tool_id: string;
  version?: string;
  tenant_id?: string;
  reason: string;
}

// ============================================================================
// Tier 2b: registry admin plane. Shapes mirror internal/domain/types.go +
// internal/api/handlers_tools.go / handlers_discovery.go / handlers_admin.go.
// ============================================================================

export interface ToolDTO {
  tool_id: string;
  display_name: string;
  owner_service: string;
  owner_team: string;
  enabled_by_default: boolean;
  side_effects: string;
  tags: string[];
  created_at?: string;
  updated_at?: string;
}

export interface RegisterToolBody {
  tool_id: string;
  display_name?: string;
  owner_service: string;
  owner_team?: string;
  enabled_by_default?: boolean;
  side_effects?: string;
  tags?: string[];
}

export interface ToolVersionDTO {
  tool_id: string;
  version: string;
  status: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  semantic_description?: string;
  permission_tier?: string;
  cost_weight?: number;
  declared_sla?: { p95_ms?: number; error_rate_pct?: number };
  side_effects?: string;
  deprecation_ends_at?: string | null;
  published_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface AddToolVersionBody {
  version: string;
  semantic_description: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  permission_tier?: string;
  cost_weight?: number;
  declared_sla?: { p95_ms?: number; error_rate_pct?: number };
  side_effects?: string;
  examples?: { input?: Record<string, unknown>; description?: string }[];
}

export interface ToolSchemaDTO {
  tool_id: string;
  version: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
}

/** GET /tools/{id}/health — per-version declared SLA + rolling health snapshot. */
export interface ToolHealthDTO {
  tool_id: string;
  versions: {
    version: string;
    status: string;
    declared_sla?: Record<string, unknown>;
    health?: Record<string, unknown>;
  }[];
}

export interface TenantToolSettingsDTO {
  tenant_id?: string;
  tool_id: string;
  enabled: boolean;
  max_tier_override?: string;
  argument_constraints?: Record<string, unknown>;
  rate_limit_override?: { per_min?: number } | null;
  updated_at?: string;
}

export interface SetEnablementBody {
  enabled: boolean;
  max_tier_override?: string;
  argument_constraints?: Record<string, unknown>;
  rate_limit_override?: { per_min: number };
}

export interface BYOSubmissionDTO {
  id: string;
  manifest?: Record<string, unknown>;
  endpoint_url: string;
  auth_method: string;
  requested_tier: string;
  egress_description?: string;
  status: string;
  decided_by?: string;
  decision_message?: string;
  created_at?: string;
}

export interface SubmitBYOBody {
  manifest?: Record<string, unknown>;
  endpoint_url: string;
  auth_method?: string;
  requested_tier?: string;
  data_egress_description?: string;
}

export class ToolPlaneClient {
  constructor(private readonly http: ServiceClient) {}

  // ---- Tier 2b: catalog / lifecycle (TPL-FR-001/002/003) --------------------

  tools(limit: number, cursor?: string, ownerService?: string): Promise<Page<ToolDTO>> {
    return this.http.get<Page<ToolDTO>>("/api/v1/tools", {
      query: { limit, cursor, "filter[owner_service]": ownerService },
    });
  }

  /** POST /tools — register a catalog tool (201). Needs tool.tool.create. */
  async registerTool(body: RegisterToolBody, idempotencyKey?: string): Promise<ToolDTO> {
    const r = await this.http.post<{ data: ToolDTO }>("/api/v1/tools", { body, idempotencyKey });
    return r.data;
  }

  /** POST /tools/{id}/versions — create a draft version (201). Needs tool.tool.update. */
  async addToolVersion(toolId: string, body: AddToolVersionBody, idempotencyKey?: string): Promise<ToolVersionDTO> {
    const r = await this.http.post<{ data: ToolVersionDTO }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/versions`,
      { body, idempotencyKey },
    );
    return r.data;
  }

  /** POST /tools/{id}/versions/{v}/publish — validates schema + computes the
   * REAL embedding before the version becomes discoverable. Needs tool.tool.update. */
  async publishToolVersion(toolId: string, version: string, idempotencyKey?: string): Promise<ToolVersionDTO> {
    const r = await this.http.post<{ data: ToolVersionDTO }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/versions/${encodeURIComponent(version)}/publish`,
      { idempotencyKey },
    );
    return r.data;
  }

  /** POST .../deprecate — window ≥30d (default 90d). Needs tool.tool.update. */
  async deprecateToolVersion(
    toolId: string,
    version: string,
    deprecationEndsAt?: string,
    idempotencyKey?: string,
  ): Promise<{ status: string; deprecation_ends_at?: string }> {
    const r = await this.http.post<{ data: { status: string; deprecation_ends_at?: string } }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/versions/${encodeURIComponent(version)}/deprecate`,
      { body: { deprecation_ends_at: deprecationEndsAt }, idempotencyKey },
    );
    return r.data;
  }

  /** POST .../retire — window elapsed OR force+reason. Needs tool.tool.delete. */
  async retireToolVersion(
    toolId: string,
    version: string,
    force: boolean,
    reason?: string,
    idempotencyKey?: string,
  ): Promise<{ status: string }> {
    const r = await this.http.post<{ data: { status: string } }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/versions/${encodeURIComponent(version)}/retire`,
      { body: { force, reason }, idempotencyKey },
    );
    return r.data;
  }

  /** GET /tools/{id}/schema?version= — published version's schemas. Needs tool.tool.read. */
  async toolSchema(toolId: string, version?: string): Promise<ToolSchemaDTO> {
    const r = await this.http.get<{ data: ToolSchemaDTO }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/schema`,
      { query: { version } },
    );
    return r.data;
  }

  /** GET /tools/{id}/health — every version's status + SLA + rolling health.
   * Doubles as the per-tool version list for the admin UI. Needs tool.tool.read. */
  async toolHealth(toolId: string): Promise<ToolHealthDTO> {
    const r = await this.http.get<{ data: ToolHealthDTO }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/health`,
    );
    return r.data;
  }

  // ---- Tier 2b: per-tenant enablement (TPL-FR-004) --------------------------

  /** PUT /tenants/self/tools/{id} — upsert the caller-tenant's enablement.
   * Needs tool.enablement.update. */
  async setToolEnablement(toolId: string, body: SetEnablementBody, idempotencyKey?: string): Promise<TenantToolSettingsDTO> {
    const r = await this.http.put<{ data: TenantToolSettingsDTO }>(
      `/api/v1/tenants/self/tools/${encodeURIComponent(toolId)}`,
      { body, idempotencyKey },
    );
    return r.data;
  }

  // ---- Tier 2b: BYO onboarding (TPL-FR-040) ---------------------------------

  /** GET /byo?filter[status]= — the submission queue, newest first. Needs tool.byo.approve. */
  async byoSubmissions(status?: string, limit?: number): Promise<BYOSubmissionDTO[]> {
    const r = await this.http.get<{ data: BYOSubmissionDTO[] }>("/api/v1/byo", {
      query: { "filter[status]": status, limit },
    });
    return r.data ?? [];
  }

  /** POST /byo — submit an external tool for review (201). Needs tool.byo.create. */
  async submitBYO(body: SubmitBYOBody, idempotencyKey?: string): Promise<BYOSubmissionDTO> {
    const r = await this.http.post<{ data: BYOSubmissionDTO }>("/api/v1/byo", { body, idempotencyKey });
    return r.data;
  }

  /** POST /byo/{id}/approve|reject — operator decision. Needs tool.byo.approve. */
  async decideBYO(
    id: string,
    decision: "approve" | "reject",
    message?: string,
    idempotencyKey?: string,
  ): Promise<{ id: string; status: string; decided_by: string }> {
    const r = await this.http.post<{ data: { id: string; status: string; decided_by: string } }>(
      `/api/v1/byo/${encodeURIComponent(id)}/${decision}`,
      { body: { message }, idempotencyKey },
    );
    return r.data;
  }

  /** GET /kill-switches — every active kill (platform-scoped table). Needs tool.kill.read. */
  async killSwitches(): Promise<ToolKillSwitchDTO[]> {
    const r = await this.http.get<{ data: ToolKillSwitchDTO[] }>("/api/v1/kill-switches");
    return r.data ?? [];
  }

  /** POST /kill-switches — set a kill (201). Needs tool.kill.create. */
  async createKillSwitch(body: CreateToolKillBody, idempotencyKey?: string): Promise<ToolKillSwitchDTO> {
    const r = await this.http.post<{ data: { id: string; active: boolean; set_by: string } }>(
      "/api/v1/kill-switches",
      { body, idempotencyKey },
    );
    return {
      id: r.data.id,
      active: r.data.active,
      set_by: r.data.set_by,
      scope: body.scope,
      tool_id: body.tool_id,
      version: body.version,
      tenant_id: body.tenant_id,
      reason: body.reason,
    };
  }

  /** DELETE /kill-switches/{id} — lift a kill (200). Needs tool.kill.delete. */
  async deleteKillSwitch(id: string): Promise<{ id: string; active: boolean }> {
    const r = await this.http.delete<{ data: { id: string; active: boolean } }>(
      `/api/v1/kill-switches/${encodeURIComponent(id)}`,
    );
    return r.data;
  }
}

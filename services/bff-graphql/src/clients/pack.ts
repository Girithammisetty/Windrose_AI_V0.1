/** pack-service REST client (BRD 23). Backs: Pack catalog + install lifecycle. */
import { ServiceClient } from "./base.js";

export interface PackSummaryDTO {
  name: string;
  version: string;
  description: string;
  publisher?: { id?: string; name?: string };
  categories?: string[];
  regulatory?: string[];
  components?: Record<string, number>;
  deferred_kinds?: string[];
}

export interface PackDeferredDTO {
  kind: string;
  reason: string;
}

export interface PackDetailDTO extends PackSummaryDTO {
  deferred?: PackDeferredDTO[];
}

/** One operation in a dry-run install plan. */
export interface PlanOpDTO {
  kind: string;
  identity: string;
  name?: string;
  action: string; // create | exists | deferred
  detail?: string;
}

/** One row of the materialization ledger. */
export interface LedgerRowDTO {
  id: string;
  kind: string;
  identity: string;
  target_urn?: string | null;
  target_id?: string | null;
  origin: string;
  action: string;
  detail?: string | null;
  reversible: boolean;
  tombstoned: boolean;
}

export interface InstallDTO {
  id: string;
  pack: string;
  version: string;
  workspaceId: string;
  status: string;
  plan?: PlanOpDTO[];
  summary?: Record<string, number>;
  createdBy?: string | null;
  createdAt?: string | null;
  ledger?: LedgerRowDTO[];
}

/** POST /installs?dry_run=true result. */
export interface InstallPlanDTO {
  pack: string;
  version: string;
  workspace_id: string;
  dry_run: boolean;
  plan: PlanOpDTO[];
}

/** POST /installs (execute) result. */
export interface InstallResultDTO {
  id: string;
  pack: string;
  version: string;
  workspace_id: string;
  status: string;
  summary: Record<string, number>;
  ledger: LedgerRowDTO[];
}

export interface UninstallResultDTO {
  id: string;
  status: string;
  reversed: number;
  tombstoned: number;
  outcomes: { ledger_id: string; deleted: boolean; detail: string }[];
}

/** Pack install/complete materialize into Core (dataset ingestion + profiling,
 * semantic submit, dashboard chart warming) — deliberately long-running, so
 * these calls override the BFF's default 10s downstream cap (BFF-FR-032). */
const PLAN_TIMEOUT = 60_000;
const INSTALL_TIMEOUT = 300_000;

export class PackClient {
  constructor(private readonly http: ServiceClient) {}

  async packs(): Promise<PackSummaryDTO[]> {
    const r = await this.http.get<{ data: PackSummaryDTO[] }>("/api/v1/packs");
    return r.data ?? [];
  }

  async pack(name: string): Promise<PackDetailDTO> {
    const r = await this.http.get<{ data: PackDetailDTO }>(
      `/api/v1/packs/${encodeURIComponent(name)}`,
    );
    return r.data;
  }

  /** Dry-run: compute the install plan with no side effect. */
  async plan(pack: string, workspaceId: string, version?: string): Promise<InstallPlanDTO> {
    const r = await this.http.post<{ data: InstallPlanDTO }>("/api/v1/installs", {
      body: { pack, version, workspace_id: workspaceId, dry_run: true },
      timeoutMs: PLAN_TIMEOUT,
    });
    return r.data;
  }

  /** Execute the install (materializes AS the caller — the JWT is forwarded). */
  async install(
    pack: string, workspaceId: string, version?: string, idempotencyKey?: string,
  ): Promise<InstallResultDTO> {
    const r = await this.http.post<{ data: InstallResultDTO }>("/api/v1/installs", {
      body: { pack, version, workspace_id: workspaceId, dry_run: false },
      idempotencyKey, timeoutMs: INSTALL_TIMEOUT,
    });
    return r.data;
  }

  async installs(workspaceId?: string): Promise<InstallDTO[]> {
    const r = await this.http.get<{ data: InstallDTO[] }>("/api/v1/installs", {
      query: workspaceId ? { workspace_id: workspaceId } : undefined,
    });
    return r.data ?? [];
  }

  async installDetail(id: string): Promise<InstallDTO> {
    const r = await this.http.get<{ data: InstallDTO }>(
      `/api/v1/installs/${encodeURIComponent(id)}`,
    );
    return r.data;
  }

  async uninstall(id: string, idempotencyKey?: string): Promise<UninstallResultDTO> {
    const r = await this.http.post<{ data: UninstallResultDTO }>(
      `/api/v1/installs/${encodeURIComponent(id)}/uninstall`,
      { idempotencyKey, timeoutMs: INSTALL_TIMEOUT },
    );
    return r.data;
  }

  /** Phase 2: after the semantic model is approved, materialize dashboards. */
  async complete(id: string, idempotencyKey?: string): Promise<CompleteResultDTO> {
    const r = await this.http.post<{ data: CompleteResultDTO }>(
      `/api/v1/installs/${encodeURIComponent(id)}/complete`,
      { idempotencyKey, timeoutMs: INSTALL_TIMEOUT },
    );
    return r.data;
  }
}

export interface CompleteResultDTO {
  id: string;
  status: string;
  dashboards: LedgerRowDTO[];
}

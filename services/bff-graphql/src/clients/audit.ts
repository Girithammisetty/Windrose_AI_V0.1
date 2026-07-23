/**
 * audit-service REST client (BRD: WORM compliance trail). Backs the admin audit
 * search surface. Pure passthrough — the caller's JWT is forwarded verbatim and
 * audit-service enforces the `audit.event.read` action guard + tenant scoping.
 * The BFF only reshapes the ClickHouse-backed event rows (snake→camel) for the UI.
 */
import { ServiceClient } from "./base.js";
import type { Page } from "./types.js";

/** audit-service eventDTO. `actor` is a nested {type,id}; `via_agent` a nested
 * {agent_id,version}. `payload` is omitted (withheld) when body_withheld is true. */
export interface AuditEventDTO {
  event_id: string;
  event_type: string;
  tenant_id: string;
  actor?: { type?: string; id?: string };
  via_agent?: { agent_id?: string; version?: string } | null;
  resource_urn?: string;
  action?: string;
  occurred_at: string;
  ingested_at?: string;
  trace_id?: string;
  payload_digest?: string;
  payload?: unknown;
  body_withheld?: boolean;
  chain_seq?: number;
  chain_hash?: string;
}

/** Filters supported by GET /api/v1/audit/search. `from`/`to` are required by the
 * backend (RFC3339, max 92-day window); the resolver defaults them when omitted. */
export interface AuditSearchQuery {
  from: string;
  to: string;
  actorId?: string;
  actorType?: string;
  action?: string;
  eventType?: string;
  resourceUrn?: string;
  resourceMatch?: string;
  traceId?: string;
  limit: number;
  cursor?: string;
}

/** POST /api/v1/audit/verify response (chain.VerifyResult, no envelope). */
export interface ChainVerifyResultDTO {
  valid: boolean;
  events_checked: number;
  chain_head: string;
  manifest_match: boolean;
  first_mismatch_seq?: number | null;
  sealed: boolean;
}

/** POST /compliance/{soc2-pack,ai-decision-log} response (202, no envelope) —
 * an async job; poll GET /operations/{id} for the download link. */
export interface ComplianceJobDTO {
  operation_id: string;
  status: string;
}

/** GET /api/v1/operations/{id} response (no envelope). status: running |
 * succeeded | failed. result_url is a presigned download link, only present
 * once succeeded. */
export interface OperationDTO {
  operation_id: string;
  status: string;
  result_url?: string;
  error?: string;
}

/** BRD 59 WS2: /audit/siemconfig row DTO. */
export interface SiemConfigDTO {
  id: string;
  endpoint: string;
  format: "CEF" | "LEEF" | "JSON";
  auth_ref?: string;
  active: boolean;
  status: string;
  requested_by: string;
  approved_by?: string;
  rejected_by?: string;
  reject_reason?: string;
  created_at: string;
  updated_at: string;
}

/** GET /audit/siemconfig response shape. */
export interface SiemConfigStateDTO {
  active: SiemConfigDTO | null;
  pending: SiemConfigDTO | null;
  history: SiemConfigDTO[];
}

/** BRD 60 WS5 — auditor evidence pack for one decision. Mirrors
 * audit-service internal/compliance/evidence.go (snake_case wire shape). */
export interface EvidencePackDTO {
  kind: string;
  tenant_id: string;
  proposal_id: string;
  proposal_urn: string;
  generated_at: string;
  decision: {
    agent_id: string;
    agent_version: string;
    on_behalf_of: string;
    approver: string;
    outcome: string;
    four_eyes: boolean;
    proposed_at: string;
    decided_at: string;
    tool_id: string;
    tool_version: string;
    args_digest: string;
    affected_urns: string[] | null;
  };
  events: Array<{
    event_id: string;
    event_type: string;
    resource_urn: string;
    actor_type: string;
    actor_id: string;
    via_agent_id?: string;
    obo_user_id?: string;
    occurred_at: string;
    payload_digest: string;
    chain_date: string;
    chain_seq: number;
    chain_hash: string;
  }>;
  chain_proof: Array<{
    chain_date: string;
    sealed: boolean;
    valid: boolean;
    manifest_match: boolean;
    events_checked: number;
    manifest_uri?: string;
    manifest_sha256?: string;
    note?: string;
  }>;
  integrity: string;
}

export class AuditClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /api/v1/audit/search — filtered, keyset-paginated event list. */
  search(q: AuditSearchQuery): Promise<Page<AuditEventDTO>> {
    return this.http.get<Page<AuditEventDTO>>("/api/v1/audit/search", {
      query: {
        from: q.from,
        to: q.to,
        actor_id: q.actorId,
        actor_type: q.actorType,
        action: q.action,
        event_type: q.eventType,
        resource_urn: q.resourceUrn,
        resource_match: q.resourceMatch,
        trace_id: q.traceId,
        limit: q.limit,
        cursor: q.cursor,
      },
    });
  }

  /** POST /api/v1/audit/verify — real chain-integrity check for one tenant-day.
   * 409 if the day isn't sealed yet (surfaced verbatim, never faked as
   * pass/fail). Needs audit.chain.execute. */
  verifyChain(date: string, tenantId?: string): Promise<ChainVerifyResultDTO> {
    return this.http.post<ChainVerifyResultDTO>("/api/v1/audit/verify", {
      body: { date, tenant_id: tenantId },
    });
  }

  /** POST /api/v1/compliance/soc2-pack (202) — kicks off an async report
   * build. Needs audit.compliance.read. */
  generateSoc2Pack(from: string, to: string): Promise<ComplianceJobDTO> {
    return this.http.post<ComplianceJobDTO>("/api/v1/compliance/soc2-pack", { body: { from, to } });
  }

  /** POST /api/v1/compliance/ai-decision-log (202). \`agentId\` is optional
   * (unscoped = all agents). Needs audit.compliance.read. */
  generateAiDecisionLog(from: string, to: string, agentId?: string): Promise<ComplianceJobDTO> {
    return this.http.post<ComplianceJobDTO>("/api/v1/compliance/ai-decision-log", {
      body: { from, to, agent_id: agentId },
    });
  }

  /** GET /api/v1/operations/{id} — poll an async compliance-pack job. Needs
   * audit.compliance.read. */
  operation(id: string): Promise<OperationDTO> {
    return this.http.get<OperationDTO>(`/api/v1/operations/${encodeURIComponent(id)}`);
  }

  /** POST /api/v1/compliance/evidence-pack (BRD 60 WS5) — synchronous,
   * single-decision auditor evidence pack (four-eyes summary + every WORM
   * event's chain position + per-day tamper-evidence). Needs
   * audit.compliance.read. */
  evidencePack(proposalId: string): Promise<EvidencePackDTO> {
    return this.http.post<EvidencePackDTO>("/api/v1/compliance/evidence-pack", {
      body: { proposal_id: proposalId },
    });
  }

  /** GET /api/v1/audit/siemconfig — the tenant's SIEM export state (BRD 59
   * WS2). Needs audit.siemconfig.read. */
  siemConfig(): Promise<SiemConfigStateDTO> {
    return this.http.get<SiemConfigStateDTO>("/api/v1/audit/siemconfig");
  }

  /** POST /api/v1/audit/siemconfig — propose a new destination (four-eyes;
   * does not take effect until a distinct admin approves it). Needs
   * audit.siemconfig.create. */
  proposeSiemConfig(body: { endpoint: string; format: string; auth_ref?: string }): Promise<SiemConfigDTO> {
    return this.http.post<SiemConfigDTO>("/api/v1/audit/siemconfig", { body });
  }

  /** POST /api/v1/audit/siemconfig/{id}/approve. Needs audit.siemconfig.approve. */
  approveSiemConfig(id: string): Promise<SiemConfigDTO> {
    return this.http.post<SiemConfigDTO>(`/api/v1/audit/siemconfig/${encodeURIComponent(id)}/approve`, {});
  }

  /** POST /api/v1/audit/siemconfig/{id}/reject. Needs audit.siemconfig.approve. */
  rejectSiemConfig(id: string, reason?: string): Promise<SiemConfigDTO> {
    return this.http.post<SiemConfigDTO>(`/api/v1/audit/siemconfig/${encodeURIComponent(id)}/reject`, {
      body: { reason },
    });
  }

  /** DELETE /api/v1/audit/siemconfig/{id}. Needs audit.siemconfig.delete. */
  deleteSiemConfig(id: string): Promise<void> {
    return this.http.delete<void>(`/api/v1/audit/siemconfig/${encodeURIComponent(id)}`);
  }
}

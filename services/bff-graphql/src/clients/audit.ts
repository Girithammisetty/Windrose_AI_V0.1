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
}

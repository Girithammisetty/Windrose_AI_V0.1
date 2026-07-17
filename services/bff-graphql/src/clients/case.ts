/** case-service REST client (BRD 08). Backs: Case, Disposition. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface CaseDTO {
  id: string;
  workspace_id?: string;
  case_number?: number;
  title?: string;
  status?: string; // draft|in_progress|resolved|unassigned|closed
  severity?: string; // low|medium|high|critical
  /** CRUD/detail view field name. */
  assigned_to_id?: string | null;
  /** Search-projection (OpenSearch doc) field name for the same value. */
  assignee_id?: string | null;
  dataset_urn?: string;
  dataset_version?: string;
  row_pk?: string;
  /** Pack/createCases-provided evidence summary (present on search docs AND
   * the CRUD view; 'note' carries the investigator briefing). */
  display_projection?: Record<string, string>;
  due_date?: string;
  description?: string;
  custom_fields?: Record<string, unknown>;
  disposition_id?: string | null;
  resolution_note?: string;
  resolved_at?: string | null;
  closed_at?: string | null;
  reassign_count?: number;
  case_version?: number;
  created_at?: string;
  updated_at?: string;
}

/** POST /cases response: what was created, and which rows deduplicated to an
 * existing case (recurrence) rather than creating a new one. */
export interface CreateCasesDTO {
  created: { id: string; case_number?: number; status?: string; dedup_key?: string; recurrence_of?: string | null }[];
  deduplicated: { id: string; case_number?: number; row_pk?: string; source_query_urns?: string[] }[];
}

// ---------------------------------------------------------------------------
// Tier 4b: case ops — lifecycle transitions, comments, timeline, export,
// disposition catalog, custom case-fields, SLA policy (case-service BRD 08).
// ---------------------------------------------------------------------------

/** A case comment (case-service Comment). NOTE: case-service exposes NO
 * "list comments" route — a comment body is only ever available on this create
 * response; the timeline carries the comment_id only. */
export interface CaseCommentDTO {
  id: string;
  case_id?: string;
  author_id?: string;
  body?: string;
  edited_at?: string | null;
  created_at?: string;
}

/** One case evidence attachment (case-service GET /cases/{id}/evidence, task #77). */
export interface CaseEvidenceDTO {
  id: string;
  case_id?: string;
  filename?: string;
  content_type?: string;
  size_bytes?: number;
  uploaded_by?: string;
  created_at?: string;
}

/** One timeline entry (case-service Activity, CASE-FR-025). `comment.added`
 * events carry {comment_id} in new_value. */
export interface CaseActivityDTO {
  id: string;
  case_id?: string;
  event_type?: string;
  actor_type?: string; // user|agent|system
  actor_id?: string;
  via_agent?: { agent_id?: string; version?: string } | null;
  proposal_urn?: string;
  old_value?: unknown;
  new_value?: unknown;
  occurred_at?: string;
}

/** An async bulk/export operation record (case-service GET /operations/{id}).
 * On export success `result` carries row_count / object_ref / download_url /
 * expires_at; on failure it carries {error}. */
export interface CaseOperationDTO {
  id: string;
  kind?: string;
  status?: string; // running|succeeded|failed
  succeeded?: number;
  failed?: number;
  total?: number;
  result?: {
    row_count?: number;
    object_ref?: string;
    download_url?: string;
    expires_at?: string;
    error?: string;
  } | null;
}

/** A workspace disposition catalog entry (case-service, CASE-FR-020). */
export interface DispositionDTO {
  id: string;
  workspace_id?: string;
  code?: string;
  label?: string;
  category?: string; // true_positive|false_positive|benign|inconclusive|other
  requires_note?: boolean;
  active?: boolean;
  created_at?: string;
  updated_at?: string;
}

/** A custom case-field config (case-service, CASE-FR-022). The service
 * serializes `purpose` as int16 (0=create, 1=update, 2=both) even though the
 * CREATE request takes the string form — the BFF mapper converts back. */
export interface CaseFieldDTO {
  id: string;
  workspace_id?: string;
  query_urn?: string;
  name?: string;
  data_type?: string; // string|text|integer|float|boolean|date|enum
  purpose?: number;
  field_meta?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

/** The thin PUT /sla-policy echo (case-service, CASE-FR-012). There is NO GET
 * route for the current policy — this is only ever the write response. */
export interface CaseSlaPolicyDTO {
  workspace_id?: string;
  warn_before_seconds?: number;
  on_breach?: string; // auto_unassign|escalate|notify_only
  max_reassign_count?: number;
}

export interface CaseListParams {
  q?: string;
  status?: string;
  assignee?: string;
  severity?: string;
  limit: number;
  cursor?: string;
}

export interface CasePatch {
  description?: string;
  due_date?: string;
  severity?: string;
  custom_fields?: Record<string, unknown>;
}

export class CaseClient {
  constructor(private readonly http: ServiceClient) {}

  async case(id: string, withRow = false): Promise<CaseDTO> {
    const r = await this.http.get<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}`,
      { query: { with_row: withRow } },
    );
    return unwrap<CaseDTO>(r);
  }

  /** Batch hydration for caseById loader: GET /cases?filter[id]=… */
  async casesByIds(ids: string[]): Promise<CaseDTO[]> {
    const res = await this.http.get<Page<CaseDTO>>("/api/v1/cases", {
      query: { "filter[id]": ids.join(","), limit: ids.length },
    });
    return res.data ?? [];
  }

  search(p: CaseListParams): Promise<Page<CaseDTO>> {
    return this.http.get<Page<CaseDTO>>("/api/v1/cases", {
      query: {
        q: p.q,
        "filter[status]": p.status,
        "filter[assignee]": p.assignee,
        "filter[severity]": p.severity,
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  /** POST /cases — materialize 1..500 cases from query/dataset rows, dedup-aware
   * on (dataset_urn, row_pk) (needs case.case.create). Returns the created and
   * deduplicated (recurrence) summaries. */
  async createCases(
    body: {
      dataset_urn: string;
      dataset_version?: string;
      query_urn?: string;
      dashboard_urn?: string;
      due_date: string;
      severity?: string;
      assigned_to_id?: string;
      description?: string;
      rows: { row_pk: string; display_projection: Record<string, string> }[];
    },
    idempotencyKey?: string,
  ): Promise<CreateCasesDTO> {
    const r = await this.http.post<{ data: CreateCasesDTO } | CreateCasesDTO>(
      "/api/v1/cases",
      { body, idempotencyKey },
    );
    return unwrap<CreateCasesDTO>(r);
  }

  /** PATCH /cases/{id} — mutation passthrough, returns the full resource. */
  async update(id: string, patch: CasePatch, idempotencyKey?: string): Promise<CaseDTO> {
    const r = await this.http.patch<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}`,
      { body: patch, idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  // ---- Tier 4b: lifecycle transitions ---------------------------------------
  // All seven respond 200 with the FULL caseView in {data}; an illegal
  // from-state is the service's real 409 INVALID_TRANSITION (surfaced verbatim
  // by the http client — never masked here).

  /** POST /cases/{id}/assign — assign/reassign (rbac case.case.assign). */
  async assign(id: string, assigneeId: string, idempotencyKey?: string): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/assign`,
      { body: { assignee_id: assigneeId }, idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** POST /cases/{id}/unassign — draft|in_progress → unassigned (case.case.assign). */
  async unassign(id: string, idempotencyKey?: string): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/unassign`,
      { idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** POST /cases/{id}/start — draft → in_progress (case.case.execute). */
  async start(id: string, idempotencyKey?: string): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/start`,
      { idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** POST /cases/{id}/resolve — in_progress → resolved (case.case.update).
   * Requires an ACTIVE disposition; the service 422s DISPOSITION_REQUIRED /
   * DISPOSITION_NOTE_REQUIRED when the catalog entry demands a note. */
  async resolve(
    id: string,
    body: { disposition_id: string; resolution_note?: string },
    idempotencyKey?: string,
  ): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/resolve`,
      { body, idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** POST /cases/{id}/reopen — resolved → in_progress within 30 days of
   * resolved_at (case.case.update). */
  async reopen(id: string, idempotencyKey?: string): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/reopen`,
      { idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** POST /cases/{id}/close — resolved → closed, terminal (case.case.update). */
  async close(id: string, idempotencyKey?: string): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/close`,
      { idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** POST /cases/{id}/escalate — bumps severity one level, status unchanged;
   * both body fields optional (case.case.update). */
  async escalate(
    id: string,
    body: { to?: string; reason?: string },
    idempotencyKey?: string,
  ): Promise<CaseDTO> {
    const r = await this.http.post<{ data: CaseDTO } | CaseDTO>(
      `/api/v1/cases/${encodeURIComponent(id)}/escalate`,
      { body, idempotencyKey },
    );
    return unwrap<CaseDTO>(r);
  }

  /** GET /cases/{id}/evidence — a case's evidence attachments metadata
   * (task #77; needs case.evidence.read). */
  async listEvidence(caseId: string): Promise<CaseEvidenceDTO[]> {
    const r = await this.http.get<{ data: CaseEvidenceDTO[] } | CaseEvidenceDTO[]>(
      `/api/v1/cases/${encodeURIComponent(caseId)}/evidence`,
    );
    const d = (r as { data?: CaseEvidenceDTO[] }).data ?? (r as CaseEvidenceDTO[]);
    return d ?? [];
  }

  // ---- Tier 4b: comments + timeline -----------------------------------------

  /** POST /cases/{id}/comments (201; body 1..8192 bytes; case.case.update). */
  async addComment(caseId: string, body: string, idempotencyKey?: string): Promise<CaseCommentDTO> {
    const r = await this.http.post<{ data: CaseCommentDTO } | CaseCommentDTO>(
      `/api/v1/cases/${encodeURIComponent(caseId)}/comments`,
      { body: { body }, idempotencyKey },
    );
    return unwrap<CaseCommentDTO>(r);
  }

  /** PATCH /comments/{cid} — author-only within 15 min (403 otherwise). The
   * route echoes ONLY {id, body} — never the full comment. */
  async editComment(commentId: string, body: string): Promise<{ id: string; body: string }> {
    const r = await this.http.patch<{ data: { id: string; body: string } } | { id: string; body: string }>(
      `/api/v1/comments/${encodeURIComponent(commentId)}`,
      { body: { body } },
    );
    return unwrap<{ id: string; body: string }>(r);
  }

  /** DELETE /comments/{cid} (204; same author-only 15-min guard). */
  async deleteComment(commentId: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/comments/${encodeURIComponent(commentId)}`);
  }

  /** GET /cases/{id}/timeline — merged event+comment feed (case.case.read).
   * Cursor is the RFC3339Nano occurred_at of the last row of the prior page. */
  timeline(caseId: string, limit: number, cursor?: string): Promise<Page<CaseActivityDTO>> {
    return this.http.get<Page<CaseActivityDTO>>(
      `/api/v1/cases/${encodeURIComponent(caseId)}/timeline`,
      { query: { limit, cursor } },
    );
  }

  // ---- Tier 4b: export + operations -----------------------------------------

  /** POST /cases/export (202 → {operation_id}; case.case.export; max 5
   * concurrent per tenant → real 429). The export worker honours ONLY the
   * `status` filter key (handlers_bulk.go statusesFromFilter) — other keys are
   * accepted but ignored, so callers should send `status` alone. */
  async exportCases(
    filter: Record<string, string>,
    format: string,
    idempotencyKey?: string,
  ): Promise<{ operation_id: string }> {
    const r = await this.http.post<{ data: { operation_id: string } } | { operation_id: string }>(
      "/api/v1/cases/export",
      { body: { filter, format }, idempotencyKey },
    );
    return unwrap<{ operation_id: string }>(r);
  }

  /** GET /operations/{id} — poll a bulk/export operation (case.case.read). */
  async operation(id: string): Promise<CaseOperationDTO> {
    const r = await this.http.get<{ data: CaseOperationDTO } | CaseOperationDTO>(
      `/api/v1/operations/${encodeURIComponent(id)}`,
    );
    return unwrap<CaseOperationDTO>(r);
  }

  // ---- Tier 4b: disposition catalog ------------------------------------------

  /** GET /dispositions — workspace catalog via the JWT workspace_id claim
   * (case.disposition.read). */
  async dispositions(): Promise<DispositionDTO[]> {
    const r = await this.http.get<Page<DispositionDTO>>("/api/v1/dispositions");
    return r.data ?? [];
  }

  /** POST /dispositions (201; duplicate code → 409; case.disposition.create). */
  async createDisposition(
    body: { code: string; label: string; category: string; requires_note?: boolean; active?: boolean },
    idempotencyKey?: string,
  ): Promise<DispositionDTO> {
    const r = await this.http.post<{ data: DispositionDTO } | DispositionDTO>(
      "/api/v1/dispositions",
      { body, idempotencyKey },
    );
    return unwrap<DispositionDTO>(r);
  }

  /** PATCH /dispositions/{id} — partial update (case.disposition.update).
   * NB: the handler always overwrites requires_note from the body, so callers
   * must send the intended value even when "unchanged". */
  async updateDisposition(
    id: string,
    body: { label?: string; category?: string; requires_note?: boolean; active?: boolean },
  ): Promise<DispositionDTO> {
    const r = await this.http.patch<{ data: DispositionDTO } | DispositionDTO>(
      `/api/v1/dispositions/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<DispositionDTO>(r);
  }

  // ---- Tier 4b: custom case-fields -------------------------------------------

  /** GET /case-fields?query_urn= (case.case.read). */
  async caseFields(queryUrn?: string): Promise<CaseFieldDTO[]> {
    const r = await this.http.get<Page<CaseFieldDTO>>("/api/v1/case-fields", {
      query: { query_urn: queryUrn },
    });
    return r.data ?? [];
  }

  /** POST /case-fields (201; case.case.update). `purpose` is the STRING form
   * here (create|update|both) — the response serializes it back as int16. */
  async createCaseField(
    body: {
      query_urn?: string;
      name: string;
      data_type: string;
      purpose?: string;
      field_meta?: Record<string, unknown>;
    },
    idempotencyKey?: string,
  ): Promise<CaseFieldDTO> {
    const r = await this.http.post<{ data: CaseFieldDTO } | CaseFieldDTO>(
      "/api/v1/case-fields",
      { body, idempotencyKey },
    );
    return unwrap<CaseFieldDTO>(r);
  }

  /** PATCH /case-fields/{id} (case.case.update). Only `purpose` (STRING form
   * create|update|both) and `field_meta` are editable; name/data_type/query_urn
   * are immutable and the handler rejects any attempt to change them. */
  async updateCaseField(
    id: string,
    body: { purpose?: string; field_meta?: Record<string, unknown> },
  ): Promise<CaseFieldDTO> {
    const r = await this.http.patch<{ data: CaseFieldDTO } | CaseFieldDTO>(
      `/api/v1/case-fields/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<CaseFieldDTO>(r);
  }

  /** DELETE /case-fields/{id}?orphan=true (204; 409 FIELD_IN_USE when values
   * exist on open cases and orphaning wasn't requested; case.case.update). */
  async deleteCaseField(id: string, orphan?: boolean): Promise<void> {
    await this.http.delete<void>(`/api/v1/case-fields/${encodeURIComponent(id)}`, {
      query: { orphan: orphan ? "true" : undefined },
    });
  }

  // ---- Tier 4b: SLA policy -----------------------------------------------------

  /** PUT /sla-policy (case.case.admin). Only non-zero/non-empty fields override
   * the platform defaults (24h warn, auto_unassign, 3 reassigns); the echo is
   * the effective stored policy. There is NO GET route to read it back. */
  async putSlaPolicy(body: {
    warn_before_seconds?: number;
    on_breach?: string;
    escalate_to?: string;
    max_reassign_count?: number;
  }): Promise<CaseSlaPolicyDTO> {
    const r = await this.http.put<{ data: CaseSlaPolicyDTO } | CaseSlaPolicyDTO>(
      "/api/v1/sla-policy",
      { body },
    );
    return unwrap<CaseSlaPolicyDTO>(r);
  }

  /** POST /cases/bulk (id-based path, ≤500 ids) — partial-failure semantics
   * (CASE-FR-030/031): 200 with {succeeded, failed} whenever at least one id
   * succeeds, 422 (thrown by the http client) only when every id fails. The
   * response has NO `data` envelope (writeJSON, not writeData) — do not
   * unwrap() it. */
  async bulk(
    caseIds: string[],
    operation: string,
    params: Record<string, unknown> | undefined,
    idempotencyKey?: string,
  ): Promise<BulkCaseResultDTO> {
    return this.http.post<BulkCaseResultDTO>("/api/v1/cases/bulk", {
      body: { operation, case_ids: caseIds, params },
      idempotencyKey,
    });
  }
}

export interface BulkCaseFailureDTO {
  id: string;
  code: string;
  message: string;
}

export interface BulkCaseResultDTO {
  succeeded?: string[];
  failed?: BulkCaseFailureDTO[];
}

/** semantic-service REST client (BRD 06). Backs: SemanticModel and its
 * published dimensions[]/measures[] — the metadata the no-code chart editor
 * offers as REAL dimension/measure pickers (instead of free-text field names).
 *
 * Pure passthrough — the caller's JWT is forwarded verbatim by ServiceClient and
 * semantic-service enforces every `semantic.model.*` action guard. The BFF makes
 * no authz/business decision here; it only reshapes the REST payloads for the UI
 * (BFF-FR-003/010/011). */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";
import { DownstreamError } from "../errors/errors.js";

/** GET /models list item (domain.model_payload) — headers only, no definition. */
export interface SemanticModelDTO {
  id: string;
  workspace_id?: string;
  name: string;
  description?: string | null;
  published_version_id?: string | null;
  published_version_no?: number | null;
  health?: { status?: string; broken_refs?: unknown[] } | null;
  created_by?: string | null;
  created_at?: string;
  updated_at?: string;
  /** Present only on POST /models's 201 response: the draft v1 it opened. */
  draft_version?: SemanticVersionDTO | null;
}

/** GET .../versions[/{version_no}] item (_version_view, models.py). `definition`
 * is included on single-version fetches and the patch-draft response; list rows
 * omit it (include_definition=false). */
export interface SemanticVersionDTO {
  id: string;
  model_id: string;
  version_no: number;
  status: "draft" | "in_review" | "published" | "rejected" | "superseded" | string;
  definition?: SemanticDefinitionDTO | null;
  diff?: unknown;
  submitted_by?: string | null;
  approved_by?: string | null;
  decision_note?: string | null;
  published_at?: string | null;
  created_at: string;
}

export interface CreateModelBody {
  workspace_id: string;
  name: string;
  description?: string;
  definition?: Record<string, unknown>;
}

export interface CompileBody {
  model: string;
  workspace_id?: string;
  metrics: string[];
  dimensions?: (string | { name: string; grain?: string })[];
  filters?: { dimension: string; op: string; values?: unknown[] }[];
  limit?: number;
  dialect?: string;
}

/** POST /compile result shape (compile_service.compile). `validation` is only
 * present when the request was made with ?validate=true AND the dry-run
 * succeeded. */
export interface CompileResultDTO {
  sql: string;
  params?: unknown[];
  engine_dialect?: string;
  output_schema?: { name: string; type?: string; role?: string }[];
  provenance?: unknown;
  warnings?: string[];
  validation?: { valid?: boolean; estimated_bytes?: number | null; verdict?: string; message?: string | null };
}

/** One dimension in a published definition (bootstrap/domain shape). */
export interface SemanticDimensionDTO {
  name: string;
  entity?: string;
  column?: string;
  type?: string; // dimension type (categorical | time | numeric | ...)
  time_grains?: string[];
  synonyms?: string[];
  origin?: string;
}

/** One measure in a published definition (bootstrap/domain shape). */
export interface SemanticMeasureDTO {
  name: string;
  entity?: string;
  agg?: string;
  expr?: string | null;
  synonyms?: string[];
  origin?: string;
}

/** The model's published definition (entities/dimensions/measures). */
export interface SemanticDefinitionDTO {
  entities?: unknown[];
  dimensions?: SemanticDimensionDTO[];
  measures?: SemanticMeasureDTO[];
  [k: string]: unknown;
}

/** GET /models/{id}/definition envelope inner shape. */
export interface SemanticDefinitionResultDTO {
  version_no?: number;
  definition?: SemanticDefinitionDTO;
}

/** A verified NL↔SQL pair (verified_queries.py vq_payload, SEM-FR-040). */
export interface VerifiedQueryDTO {
  id: string;
  workspace_id?: string;
  model_id?: string | null;
  nl_text: string;
  sql_text: string;
  variables?: unknown;
  status: "draft" | "pending_review" | "approved" | "rejected" | "archived" | string;
  tags?: string[];
  provenance?: unknown;
  health_note?: string | null;
  submitted_by?: string | null;
  approved_by?: string | null;
  decided_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** One hit from GET /verified-queries:search (VerifiedQueryService.search). */
export interface VerifiedQuerySearchHitDTO {
  id: string;
  nl_text: string;
  sql_text: string;
  variables?: unknown;
  tags?: string[];
  model_id?: string | null;
  score: number;
}

/** POST /verified-queries body (schemas.VerifiedQueryCreate). */
export interface VerifiedQueryCreateBody {
  workspace_id: string;
  nl_text: string;
  sql_text: string;
  variables?: unknown[];
  model?: string;
  tags?: string[];
}

/** PATCH /verified-queries/{id} body (only draft/rejected are editable). */
export interface VerifiedQueryPatchBody {
  nl_text?: string;
  sql_text?: string;
  variables?: unknown[];
  tags?: string[];
}

/** POST /models/{id}/bootstrap + GET /operations/{id} payload (models.py). */
export interface SemanticOperationDTO {
  operation_id: string;
  kind?: string;
  status?: string;
  report?: unknown;
  created_at?: string;
  finished_at?: string | null;
}

export class SemanticClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /models — published/registered models, optionally scoped to a workspace.
   * Returns headers only (no dimensions/measures); the editor fetches the full
   * definition via `definition(id)` once a model is picked (avoids N+1). */
  async models(workspaceId?: string, limit = 200, cursor?: string): Promise<SemanticModelDTO[]> {
    const r = await this.http.get<Page<SemanticModelDTO>>("/api/v1/models", {
      query: { "filter[workspace_id]": workspaceId, limit, cursor },
    });
    return r.data ?? [];
  }

  /** GET /models/{id} — a single model's headers. */
  async model(id: string): Promise<SemanticModelDTO> {
    const r = await this.http.get<{ data: SemanticModelDTO } | SemanticModelDTO>(
      `/api/v1/models/${encodeURIComponent(id)}`,
    );
    return unwrap<SemanticModelDTO>(r);
  }

  /** GET /models/{id}/definition — the PUBLISHED definition by default, or a
   * historical published version when `version` is given. */
  async definition(id: string, version?: number): Promise<SemanticDefinitionResultDTO> {
    const r = await this.http.get<{ data: SemanticDefinitionResultDTO } | SemanticDefinitionResultDTO>(
      `/api/v1/models/${encodeURIComponent(id)}/definition`,
      { query: { version } },
    );
    return unwrap<SemanticDefinitionResultDTO>(r);
  }

  /** GET /models — RAW page (cursor + has_more preserved), for the authoring
   * list's cursor pagination. `models()` above discards paging for the chart
   * editor's "fetch everything" use; kept separate rather than changed. */
  async listModels(workspaceId?: string, limit = 50, cursor?: string): Promise<Page<SemanticModelDTO>> {
    return this.http.get<Page<SemanticModelDTO>>("/api/v1/models", {
      query: { "filter[workspace_id]": workspaceId, limit, cursor },
    });
  }

  // ---- authoring -------------------------------------------------------------

  /** POST /models — create a model + open its draft v1 (201). Needs semantic.model.create. */
  async createModel(body: CreateModelBody, idempotencyKey?: string): Promise<SemanticModelDTO> {
    const r = await this.http.post<{ data: SemanticModelDTO } | SemanticModelDTO>("/api/v1/models", {
      body,
      idempotencyKey,
    });
    return unwrap<SemanticModelDTO>(r);
  }

  /** PATCH /models/{id} — name/description only. Needs semantic.model.update. */
  async patchModel(id: string, body: { name?: string; description?: string }): Promise<SemanticModelDTO> {
    const r = await this.http.patch<{ data: SemanticModelDTO } | SemanticModelDTO>(
      `/api/v1/models/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<SemanticModelDTO>(r);
  }

  /** DELETE /models/{id} (204). Needs semantic.model.delete. */
  async deleteModel(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/models/${encodeURIComponent(id)}`);
  }

  /** GET /models/{id}/versions — newest-first-by-version_no headers (no definition). */
  async versions(modelId: string, limit = 50, cursor?: string): Promise<Page<SemanticVersionDTO>> {
    return this.http.get<Page<SemanticVersionDTO>>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions`,
      { query: { limit, cursor } },
    );
  }

  /** GET /models/{id}/versions/{version_no} — WITH definition. */
  async version(modelId: string, versionNo: number): Promise<SemanticVersionDTO> {
    const r = await this.http.get<{ data: SemanticVersionDTO } | SemanticVersionDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${versionNo}`,
    );
    return unwrap<SemanticVersionDTO>(r);
  }

  /** POST /models/{id}/versions — open a new draft from the published definition
   * (201); 409 if a draft/in_review/rejected version is already open. Needs
   * semantic.model.update. */
  async createVersion(modelId: string, idempotencyKey?: string): Promise<SemanticVersionDTO> {
    const r = await this.http.post<{ data: SemanticVersionDTO } | SemanticVersionDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions`,
      { idempotencyKey },
    );
    return unwrap<SemanticVersionDTO>(r);
  }

  /** PATCH /models/{id}/versions/{version_no} — replace the DRAFT definition
   * (save). Runs structural/expression validation immediately (parse_definition);
   * a 422 here is a real save-time rejection, let it propagate. Needs
   * semantic.model.update. */
  async patchDraft(modelId: string, versionNo: number, definition: Record<string, unknown>): Promise<SemanticVersionDTO> {
    const r = await this.http.patch<{ data: SemanticVersionDTO } | SemanticVersionDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${versionNo}`,
      { body: { definition } },
    );
    return unwrap<SemanticVersionDTO>(r);
  }

  /** POST /models/{id}/versions/{version_no}/submit — full validation gate; 422
   * VALIDATION_FAILED with a `details: [{object, problem}]` list on failure, 409
   * on an illegal transition. Needs semantic.model.update. */
  async submit(modelId: string, versionNo: number): Promise<SemanticVersionDTO> {
    const r = await this.http.post<{ data: SemanticVersionDTO } | SemanticVersionDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${versionNo}/submit`,
      { body: {} },
    );
    return unwrap<SemanticVersionDTO>(r);
  }

  /** POST /models/{id}/versions/{version_no}/approve — publishes. 403
   * PERMISSION_DENIED if the caller authored this version (four-eyes,
   * SEM-FR-007). Needs semantic.model.approve. */
  async approve(modelId: string, versionNo: number, note?: string): Promise<SemanticVersionDTO> {
    const r = await this.http.post<{ data: SemanticVersionDTO } | SemanticVersionDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${versionNo}/approve`,
      { body: note ? { note } : {} },
    );
    return unwrap<SemanticVersionDTO>(r);
  }

  /** POST /models/{id}/versions/{version_no}/reject — `note` is REQUIRED (422
   * VALIDATION_FAILED without one). Needs semantic.model.approve. */
  async reject(modelId: string, versionNo: number, note: string): Promise<SemanticVersionDTO> {
    const r = await this.http.post<{ data: SemanticVersionDTO } | SemanticVersionDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${versionNo}/reject`,
      { body: { note } },
    );
    return unwrap<SemanticVersionDTO>(r);
  }

  // ---- verified queries (SEM-FR-040) -------------------------------------------

  /** GET /verified-queries — cursor-paginated, filterable by workspace/status. */
  verifiedQueries(p: {
    workspaceId?: string;
    status?: string;
    limit: number;
    cursor?: string;
  }): Promise<Page<VerifiedQueryDTO>> {
    return this.http.get<Page<VerifiedQueryDTO>>("/api/v1/verified-queries", {
      query: {
        "filter[workspace_id]": p.workspaceId,
        "filter[status]": p.status,
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  /** GET /verified-queries/{id}. */
  async verifiedQuery(id: string): Promise<VerifiedQueryDTO> {
    const r = await this.http.get<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      `/api/v1/verified-queries/${encodeURIComponent(id)}`,
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  /** POST /verified-queries — author a draft pair (201; needs
   * semantic.verified_query.create). */
  async createVerifiedQuery(body: VerifiedQueryCreateBody, idempotencyKey?: string): Promise<VerifiedQueryDTO> {
    const r = await this.http.post<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      "/api/v1/verified-queries",
      { body, idempotencyKey },
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  /** PATCH /verified-queries/{id} — draft/rejected only (409 otherwise; a
   * rejected pair auto-returns to draft on edit). */
  async patchVerifiedQuery(id: string, body: VerifiedQueryPatchBody): Promise<VerifiedQueryDTO> {
    const r = await this.http.patch<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      `/api/v1/verified-queries/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  /** GET /verified-queries:search — ANN over APPROVED pairs, hard tenant+workspace
   * scope (SEM-FR-041). Returns bare `{data: [...]}` (array, not unwrappable). */
  async verifiedQuerySearch(
    query: string,
    workspaceId: string,
    topK?: number,
  ): Promise<VerifiedQuerySearchHitDTO[]> {
    const r = await this.http.get<{ data: VerifiedQuerySearchHitDTO[] }>(
      "/api/v1/verified-queries:search",
      {
        query: {
          q: query,
          workspace_id: workspaceId,
          top_k: topK,
        },
      },
    );
    return r.data ?? [];
  }

  /** POST /verified-queries/{id}/submit — draft → pending_review. */
  async submitVerifiedQuery(id: string): Promise<VerifiedQueryDTO> {
    const r = await this.http.post<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      `/api/v1/verified-queries/${encodeURIComponent(id)}/submit`,
      { body: {} },
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  /** POST /verified-queries/{id}/approve — pending_review → approved. Four-eyes:
   * 403 PERMISSION_DENIED when the caller authored the pair (SEM-FR-040). */
  async approveVerifiedQuery(id: string): Promise<VerifiedQueryDTO> {
    const r = await this.http.post<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      `/api/v1/verified-queries/${encodeURIComponent(id)}/approve`,
      { body: {} },
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  /** POST /verified-queries/{id}/reject — pending_review → rejected (four-eyes). */
  async rejectVerifiedQuery(id: string, note?: string): Promise<VerifiedQueryDTO> {
    const r = await this.http.post<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      `/api/v1/verified-queries/${encodeURIComponent(id)}/reject`,
      { body: { note: note ?? null } },
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  /** POST /verified-queries/{id}/archive — terminal from any state. */
  async archiveVerifiedQuery(id: string): Promise<VerifiedQueryDTO> {
    const r = await this.http.post<{ data: VerifiedQueryDTO } | VerifiedQueryDTO>(
      `/api/v1/verified-queries/${encodeURIComponent(id)}/archive`,
      { body: {} },
    );
    return unwrap<VerifiedQueryDTO>(r);
  }

  // ---- bootstrap-from-dataset (SEM-FR-020) --------------------------------------

  /** POST /models/{id}/bootstrap — auto-draft entities/dimensions/measures from
   * dataset schemas (202; needs semantic.model.update). */
  async bootstrap(
    modelId: string,
    sources: Record<string, unknown>,
    idempotencyKey?: string,
  ): Promise<SemanticOperationDTO> {
    const r = await this.http.post<{ data: SemanticOperationDTO } | SemanticOperationDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/bootstrap`,
      { body: { sources }, idempotencyKey },
    );
    return unwrap<SemanticOperationDTO>(r);
  }

  /** GET /operations/{id} — poll a bootstrap operation. */
  async operation(id: string): Promise<SemanticOperationDTO> {
    const r = await this.http.get<{ data: SemanticOperationDTO } | SemanticOperationDTO>(
      `/api/v1/operations/${encodeURIComponent(id)}`,
    );
    return unwrap<SemanticOperationDTO>(r);
  }

  /**
   * POST /compile[?validate=true] — compile metrics+dimensions+filters to real
   * SQL (+ a query-service dry-run when `validate`). `draftVersionNo` forwards
   * X-Draft-Version so authors can preview an unpublished draft (BR-2).
   *
   * The dry-run add-on is best-effort: semantic-service folds the dry-run call
   * INTO the same request, so if it fails the whole call 500s even though the
   * compiled SQL itself is fine. When the caller asked for `validate` we first
   * try the combined call; on failure we retry WITHOUT validate so the real
   * compiled SQL/schema still comes back, and report the dry-run failure
   * honestly via the `validationError` return rather than fabricating a verdict.
   */
  async compile(
    body: CompileBody,
    opts: { validate?: boolean; draftVersionNo?: number } = {},
  ): Promise<{ result: CompileResultDTO; validationError?: string }> {
    const headers = opts.draftVersionNo != null ? { "x-draft-version": String(opts.draftVersionNo) } : undefined;
    const path = opts.validate ? "/api/v1/compile?validate=true" : "/api/v1/compile";
    try {
      const r = await this.http.post<{ data: CompileResultDTO } | CompileResultDTO>(path, { body, headers });
      return { result: unwrap<CompileResultDTO>(r) };
    } catch (e) {
      if (!opts.validate || !(e instanceof DownstreamError)) throw e;
      // Retry without the dry-run add-on so a real, unrelated compile still
      // resolves; surface the dry-run failure as text, not a fabricated verdict.
      const r = await this.http.post<{ data: CompileResultDTO } | CompileResultDTO>("/api/v1/compile", {
        body,
        headers,
      });
      return {
        result: unwrap<CompileResultDTO>(r),
        validationError: e.message || "dry-run cost estimate unavailable",
      };
    }
  }
}

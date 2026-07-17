/** pipeline-orchestrator REST client (BRD §5). Backs: PipelineStepType,
 * AlgorithmTemplate, PipelineTemplate, PipelineValidationResult, PipelineRun.
 *
 * Pure passthrough — the caller's JWT is forwarded verbatim by ServiceClient and
 * pipeline-orchestrator enforces every `pipeline.*` action guard. The BFF makes
 * no authz/business decision here; it only reshapes the REST payloads for the
 * no-code builder UI (BFF-FR-003/010/011). */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";
import { DownstreamError } from "../errors/errors.js";

// ---- catalog: components (step types) -------------------------------------
export interface StepPortDTO {
  name: string;
  type: string;
}

/** One entry in a component's `parameters` map (component.definition.parameters).
 * The backend uses `minimum`/`maximum`/`enum`; extra keys (min_length, hide_for,
 * item_description, ...) are carried through opaquely. */
export interface StepParamDTO {
  type?: string;
  required?: boolean;
  default?: unknown;
  enum?: unknown[] | null;
  minimum?: number | null;
  maximum?: number | null;
  help?: string | null;
  [k: string]: unknown;
}

export interface ComponentDTO {
  name: string;
  component_type: string; // io | data_prep | algorithm | utility | comment | other
  label?: string;
  enabled?: boolean;
  catalog_version?: string;
  image_digest?: string | null;
  min_inputs?: number | null;
  max_inputs?: number | null;
  max_outputs?: number | null;
  outputs?: StepPortDTO[] | null;
  parameters?: Record<string, StepParamDTO> | null;
}

/** GET /components returns the catalog grouped by component_type. */
export interface ComponentsCatalogDTO {
  catalog_version?: string;
  groups?: Record<string, ComponentDTO[]>;
}

// ---- catalog: algorithm templates -----------------------------------------
export interface AlgorithmDTO {
  name: string;
  label?: string;
  model_type?: string | null; // enum name: classification | regression | ...
  order?: number;
  input_type?: Record<string, unknown>; // { training, tuning, tuning_cross_validation }
  parameters?: Record<string, StepParamDTO> | null;
  tuning_parameters?: Record<string, StepParamDTO> | null;
  runnable?: boolean;
  metadata?: Record<string, unknown>;
}

// ---- pipeline templates ----------------------------------------------------
export interface TemplateDTO {
  id: string;
  workspace_id?: string;
  name: string;
  pipeline_type: string; // enum NAME: data_prep | ... | scheduled
  model_type?: string | null;
  algorithm_template_name?: string | null;
  active_version_id?: string | null;
  is_system?: boolean;
  archived?: boolean;
  validation_status?: string | null; // "valid" | "draft" (only when a version is loaded)
  validation_report?: unknown;
  manifest_digest?: string | null;
  created_by?: string | null; // not serialized by the backend today (see map.ts)
  definition?: unknown; // active version's DAG {nodes,edges}; present only on single-template reads (never on the list)
  created_at?: string;
  updated_at?: string;
}

// ---- runs ------------------------------------------------------------------
export interface PipelineRunDTO {
  id: string;
  template_id: string;
  version_id?: string;
  status: string; // RunStatus name
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  [k: string]: unknown;
}

/** One immutable template version (pipelines.py version_payload). */
export interface TemplateVersionDTO {
  id: string;
  template_id: string;
  version_no: number;
  validation_status?: string; // "valid" | "draft"
  validation_report?: unknown;
  run_parameters?: unknown;
  global_parameters?: unknown;
  component_catalog_version?: string | null;
  manifest_digest?: string | null;
  argo_template_name?: string | null;
  created_at?: string;
}

/** POST /pipelines/{id}/compile result. */
export interface CompiledManifestDTO {
  template_id?: string;
  version_id?: string;
  manifest_digest?: string | null;
  argo_template_name?: string | null;
  manifest?: unknown;
}

/** GET /runs/{id}/manifest result. */
export interface RunManifestDTO {
  run_id?: string;
  manifest?: unknown;
  resolved_parameters?: unknown;
}

// ---- validation report -----------------------------------------------------
export interface ValidationIssueDTO {
  code: string;
  alias?: string | null;
  field?: string | null;
  problem?: string;
  [k: string]: unknown;
}

/** POST /pipelines/validate report (ValidationReport.to_dict()). */
export interface ValidationReportDTO {
  status: string; // "valid" | "draft"
  items: ValidationIssueDTO[];
  [k: string]: unknown;
}

export interface PipelineListParams {
  name?: string;
  pipelineType?: string;
  /** Include soft-deleted templates (GET /pipelines?include_archived=true). */
  includeArchived?: boolean;
  limit: number;
  cursor?: string;
}

export interface RunListParams {
  status?: string;
  templateId?: string;
  limit: number;
  cursor?: string;
}

export interface CreatePipelineBody {
  workspace_id: string;
  name: string;
  pipeline_type: string;
  definition: Record<string, unknown>;
}

/** PUT /pipelines/{id} body (TemplateUpdate: all fields optional — a partial
 * update). pipeline_type is immutable; the backend derives it from the template. */
export interface UpdatePipelineBody {
  name?: string;
  definition?: Record<string, unknown>;
  run_parameters?: Record<string, unknown>;
}

/** A recurring pipeline schedule (pipeline-orchestrator schedule_payload,
 * PIPE-FR-050). `id` is the schedule_id; it fires `template_id`'s active version
 * on `cron` with `run_parameters`. */
export interface PipelineScheduleDTO {
  id: string;
  template_id: string;
  name?: string | null;
  cron: string;
  timezone?: string;
  run_parameters?: Record<string, unknown>;
  enabled?: boolean;
  next_fire_at?: string | null;
  last_fire_at?: string | null;
  last_run_id?: string | null;
  created_by?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** POST /pipeline-schedules body (schemas.ScheduleCreate). `cron` is required;
 * `timezone` defaults to UTC and `run_parameters` to {} on the server. */
export interface CreatePipelineScheduleBody {
  template_id: string;
  name?: string;
  cron: string;
  timezone?: string;
  run_parameters?: Record<string, unknown>;
}

function isEnvelopeBody(b: unknown): b is { data?: unknown; status?: unknown; items?: unknown } {
  return typeof b === "object" && b !== null;
}

/** True when a recovered 422 body is genuinely the validation *report*
 * ({status, items} possibly under `data`) and NOT the master error envelope
 * ({error:{code,...}}). The orchestrator answers 422 for BOTH a report-invalid
 * outcome AND a pydantic request-validation failure (middleware.py
 * validation_handler); only the former is a normal "definition invalid" result
 * to recover — a request error must bubble as VALIDATION_FAILED. */
function isValidationReportBody(b: unknown): boolean {
  if (!isEnvelopeBody(b)) return false;
  if ("error" in b) return false; // master error envelope — never a report
  const inner = "data" in b ? (b as { data?: unknown }).data : b;
  return typeof inner === "object" && inner !== null && typeof (inner as { status?: unknown }).status === "string";
}

export class PipelinesClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /components — the step-type catalog, grouped by type. Flattened to a
   * single list the UI palette renders from. */
  async components(): Promise<ComponentDTO[]> {
    const r = await this.http.get<{ data: ComponentsCatalogDTO }>("/api/v1/components");
    const groups = r.data?.groups ?? {};
    return Object.values(groups).flat();
  }

  /** GET /algorithm-templates — the algorithm-step catalog. */
  async algorithmTemplates(): Promise<AlgorithmDTO[]> {
    const r = await this.http.get<{ data: AlgorithmDTO[] }>("/api/v1/algorithm-templates");
    return r.data ?? [];
  }

  pipelines(p: PipelineListParams): Promise<Page<TemplateDTO>> {
    return this.http.get<Page<TemplateDTO>>("/api/v1/pipelines", {
      query: {
        "filter[name]": p.name,
        "filter[pipeline_type]": p.pipelineType,
        include_archived: p.includeArchived ? true : undefined,
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  async pipeline(id: string): Promise<TemplateDTO> {
    const r = await this.http.get<{ data: TemplateDTO } | TemplateDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}`,
    );
    return unwrap<TemplateDTO>(r);
  }

  /** POST /pipelines — create a template (201). workspace_id is sourced from the
   * caller's JWT claim by the resolver (the SDL input omits it). */
  async createPipeline(body: CreatePipelineBody, idempotencyKey?: string): Promise<TemplateDTO> {
    const r = await this.http.post<{ data: TemplateDTO } | TemplateDTO>("/api/v1/pipelines", {
      body,
      idempotencyKey,
    });
    return unwrap<TemplateDTO>(r);
  }

  /** PUT /pipelines/{id} — update a template (a new immutable version). Returns
   * the template with its new active version's definition + validation status. */
  async updatePipeline(id: string, body: UpdatePipelineBody, idempotencyKey?: string): Promise<TemplateDTO> {
    const r = await this.http.put<{ data: TemplateDTO } | TemplateDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}`,
      { body, idempotencyKey },
    );
    return unwrap<TemplateDTO>(r);
  }

  /** POST /pipelines/validate — validate a definition. The endpoint answers 200
   * with the report under `data` when valid, and 422 with the SAME report under
   * `data` when invalid (it is NOT the master error envelope). A 422 is a normal
   * "definition not valid" outcome for the builder, so we recover the report from
   * the DownstreamError body rather than letting it surface as an error. */
  async validate(body: { pipeline_type: string; definition: Record<string, unknown> }): Promise<ValidationReportDTO> {
    try {
      const r = await this.http.post<{ data: ValidationReportDTO } | ValidationReportDTO>(
        "/api/v1/pipelines/validate",
        { body },
      );
      return unwrap<ValidationReportDTO>(r);
    } catch (e) {
      if (e instanceof DownstreamError && e.httpStatus === 422 && isValidationReportBody(e.body)) {
        return unwrap<ValidationReportDTO>(e.body as { data: ValidationReportDTO } | ValidationReportDTO);
      }
      throw e;
    }
  }

  /** POST /pipelines/{id}/run — submit a run (202). Response is
   * { operation_id, data: run }. */
  async run(id: string, runParameters: Record<string, unknown>, idempotencyKey?: string): Promise<PipelineRunDTO> {
    const r = await this.http.post<{ operation_id?: string; data: PipelineRunDTO } | PipelineRunDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}/run`,
      { body: { run_parameters: runParameters }, idempotencyKey },
    );
    return unwrap<PipelineRunDTO>(r);
  }

  runs(p: RunListParams): Promise<Page<PipelineRunDTO>> {
    return this.http.get<Page<PipelineRunDTO>>("/api/v1/runs", {
      query: {
        "filter[status]": p.status,
        "filter[template_id]": p.templateId,
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  // ---- run lifecycle (PIPE-FR run controls) -----------------------------------

  /** GET /runs/{id}. */
  async runById(id: string): Promise<PipelineRunDTO> {
    const r = await this.http.get<{ data: PipelineRunDTO } | PipelineRunDTO>(
      `/api/v1/runs/${encodeURIComponent(id)}`,
    );
    return unwrap<PipelineRunDTO>(r);
  }

  /** PUT /runs/{id}/terminate — cancel a live run; a terminal run is an
   * idempotent no-op (BR-6). Needs pipeline.run.execute. */
  async terminateRun(id: string): Promise<PipelineRunDTO> {
    const r = await this.http.put<{ data: PipelineRunDTO } | PipelineRunDTO>(
      `/api/v1/runs/${encodeURIComponent(id)}/terminate`,
    );
    return unwrap<PipelineRunDTO>(r);
  }

  /** POST /runs/{id}/retry — re-submit a FAILED run (202; 409 otherwise).
   * Needs pipeline.run.create. Returns the NEW run. */
  async retryRun(id: string, idempotencyKey?: string): Promise<PipelineRunDTO> {
    const r = await this.http.post<{ operation_id?: string; data: PipelineRunDTO } | PipelineRunDTO>(
      `/api/v1/runs/${encodeURIComponent(id)}/retry`,
      { idempotencyKey },
    );
    return unwrap<PipelineRunDTO>(r);
  }

  /** GET /runs/{id}/manifest — the compiled manifest + resolved parameters. */
  async runManifest(id: string): Promise<RunManifestDTO> {
    const r = await this.http.get<{ data: RunManifestDTO } | RunManifestDTO>(
      `/api/v1/runs/${encodeURIComponent(id)}/manifest`,
    );
    return unwrap<RunManifestDTO>(r);
  }

  // ---- template lifecycle (PIPE-FR template controls) ---------------------------

  /** GET /pipelines/{id}/versions — immutable version history. */
  templateVersions(id: string, limit: number, cursor?: string): Promise<Page<TemplateVersionDTO>> {
    return this.http.get<Page<TemplateVersionDTO>>(
      `/api/v1/pipelines/${encodeURIComponent(id)}/versions`,
      { query: { limit, cursor } },
    );
  }

  /** POST /pipelines/{id}/versions/{versionId}/activate — set the active
   * version. Needs pipeline.template.update. */
  async activateVersion(templateId: string, versionId: string): Promise<TemplateDTO> {
    const r = await this.http.post<{ data: TemplateDTO } | TemplateDTO>(
      `/api/v1/pipelines/${encodeURIComponent(templateId)}/versions/${encodeURIComponent(versionId)}/activate`,
    );
    return unwrap<TemplateDTO>(r);
  }

  /** POST /pipelines/{id}/clone — copy into a new template (201). Needs
   * pipeline.template.create. */
  async clonePipeline(id: string, idempotencyKey?: string): Promise<TemplateDTO> {
    const r = await this.http.post<{ data: TemplateDTO } | TemplateDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}/clone`,
      { idempotencyKey },
    );
    return unwrap<TemplateDTO>(r);
  }

  /** POST /pipelines/{id}/compile — compile the active version to an Argo
   * manifest. Needs pipeline.template.execute. */
  async compilePipeline(id: string): Promise<CompiledManifestDTO> {
    const r = await this.http.post<{ data: CompiledManifestDTO } | CompiledManifestDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}/compile`,
    );
    return unwrap<CompiledManifestDTO>(r);
  }

  /** DELETE /pipelines/{id} — archive (soft-delete; 409 for system templates).
   * Needs pipeline.template.delete. Answers 200 with the template. */
  async deletePipeline(id: string): Promise<TemplateDTO> {
    const r = await this.http.delete<{ data: TemplateDTO } | TemplateDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}`,
    );
    return unwrap<TemplateDTO>(r);
  }

  /** PATCH /pipelines/{id}/restore — undo the soft-delete. Needs
   * pipeline.template.update. */
  async restorePipeline(id: string): Promise<TemplateDTO> {
    const r = await this.http.patch<{ data: TemplateDTO } | TemplateDTO>(
      `/api/v1/pipelines/${encodeURIComponent(id)}/restore`,
    );
    return unwrap<TemplateDTO>(r);
  }

  // ---- recurring pipeline schedules (PIPE-FR-050) -------------------------------

  /** GET /pipeline-schedules — the tenant's recurring schedules (page_envelope,
   * un-paged today). Needs pipeline.schedule.read. */
  async pipelineSchedules(): Promise<PipelineScheduleDTO[]> {
    const r = await this.http.get<{ data: PipelineScheduleDTO[] }>("/api/v1/pipeline-schedules");
    return r.data ?? [];
  }

  /** POST /pipeline-schedules — create (201). Needs pipeline.schedule.create. */
  async createPipelineSchedule(
    body: CreatePipelineScheduleBody,
    idempotencyKey?: string,
  ): Promise<PipelineScheduleDTO> {
    const r = await this.http.post<{ data: PipelineScheduleDTO } | PipelineScheduleDTO>(
      "/api/v1/pipeline-schedules",
      { body, idempotencyKey },
    );
    return unwrap<PipelineScheduleDTO>(r);
  }

  /** POST /pipeline-schedules/{id}/pause — stop firing. Needs pipeline.schedule.update. */
  async pausePipelineSchedule(id: string): Promise<PipelineScheduleDTO> {
    const r = await this.http.post<{ data: PipelineScheduleDTO } | PipelineScheduleDTO>(
      `/api/v1/pipeline-schedules/${encodeURIComponent(id)}/pause`,
    );
    return unwrap<PipelineScheduleDTO>(r);
  }

  /** POST /pipeline-schedules/{id}/resume — re-enable firing. Needs pipeline.schedule.update. */
  async resumePipelineSchedule(id: string): Promise<PipelineScheduleDTO> {
    const r = await this.http.post<{ data: PipelineScheduleDTO } | PipelineScheduleDTO>(
      `/api/v1/pipeline-schedules/${encodeURIComponent(id)}/resume`,
    );
    return unwrap<PipelineScheduleDTO>(r);
  }

  /** POST /pipeline-schedules/{id}/run-now — force one fire (202). The response
   * carries the updated schedule under `data` AND the newly created run under a
   * sibling `run` key (never `data`). Needs pipeline.schedule.execute. */
  async runNowPipelineSchedule(
    id: string,
  ): Promise<{ schedule: PipelineScheduleDTO; run: PipelineRunDTO | null }> {
    const r = await this.http.post<{ data: PipelineScheduleDTO; run?: PipelineRunDTO | null }>(
      `/api/v1/pipeline-schedules/${encodeURIComponent(id)}/run-now`,
    );
    return { schedule: r.data, run: r.run ?? null };
  }

  /** DELETE /pipeline-schedules/{id} — 204. Needs pipeline.schedule.delete. */
  async deletePipelineSchedule(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/pipeline-schedules/${encodeURIComponent(id)}`);
  }
}

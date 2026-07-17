/** inference-service REST client (BRD §5). Backs: InferenceJob.
 *
 * Pure passthrough — the caller's JWT is forwarded verbatim by ServiceClient and
 * inference-service enforces every `inference.*` action guard. The BFF makes no
 * authz/business decision here; it only reshapes the REST payloads for the UI. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

/** The model reference captured on a job at submit time (inference-service
 * job_payload `model`). `stage_at_submit` is the promotion stage the version had
 * when the job was queued (staging | production | archived | none | null). */
export interface InferenceModelRefDTO {
  urn?: string;
  name?: string | null;
  version?: number | null;
  stage_at_submit?: string | null;
}

export interface InferenceDatasetRefDTO {
  urn?: string;
  version?: number | null;
}

export interface InferenceTimestampsDTO {
  queued_at?: string | null;
  submitted_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
}

/** GET /inferences item / GET /inferences/{id} `data` (inference-service
 * job_payload). Status is the JobStatus NAME (validating | queued | submitted |
 * running | finalizing | succeeded | failed | cancelling | cancelled | rejected). */
export interface InferenceJobDTO {
  id: string;
  status?: string;
  name?: string | null;
  description?: string | null;
  model?: InferenceModelRefDTO;
  input_dataset?: InferenceDatasetRefDTO;
  output_dataset?: InferenceDatasetRefDTO | null;
  output_mode?: number;
  parameters?: Record<string, unknown> | null;
  error?: string | null;
  row_count?: number | null;
  pipeline_run_urn?: string | null;
  schedule_id?: string | null;
  /** Tier 4b: set by POST /inferences/{id}/retry on the NEW job it creates. */
  retried_from_job_id?: string | null;
  via_agent?: boolean;
  timestamps?: InferenceTimestampsDTO;
}

export interface InferenceListParams {
  status?: string;
  limit: number;
  cursor?: string;
}

/** POST /inferences body (inference-service SubmitBody). */
export interface CreateInferenceBody {
  model_version_urn: string;
  input_dataset_urn: string;
  name?: string;
  description?: string;
  parameters?: Record<string, unknown>;
  allow_unpromoted?: boolean;
  allow_empty?: boolean;
}

/** POST /inferences (202) `data` envelope: {operation_id, job_id, status}. */
interface SubmitResponse {
  operation_id?: string;
  job_id?: string;
  status?: string;
}

// ==== Tier 4b: ml ops (job lifecycle + validate + bulk + schedules) =========

/** POST /inferences/validate body (ValidateBody). */
export interface ValidateInferenceBody {
  model_version_urn: string;
  input_dataset_urn: string;
  allow_unpromoted?: boolean;
  allow_empty?: boolean;
}

/** One column verdict from schema_compat (ok | missing | type_mismatch |
 * nullable_mismatch). */
export interface CompatColumnDTO {
  name: string;
  required_type?: string;
  actual_type?: string | null;
  verdict: string;
}

/** validate `data` (CompatibilityReport.as_dict, + stage_error when the stage
 * policy alone fails the check). */
export interface CompatibilityReportDTO {
  compatible: boolean;
  model_stage?: string;
  columns?: CompatColumnDTO[];
  warnings?: unknown[];
  row_count?: number | null;
  stage_error?: string;
}

/** POST /inferences/bulk body (BulkBody, max 20 datasets). */
export interface BulkInferenceBody {
  model_version_urn: string;
  input_dataset_urns: string[];
  parameters?: Record<string, unknown>;
  output?: { dataset_name?: string; mode?: string };
}

/** One per-dataset bulk result (InferenceService.bulk): a submitted/rejected
 * job ({input_dataset_urn, job_id, status}) OR a validation failure
 * ({input_dataset_urn, error: {code, message}}). */
export interface BulkInferenceResultDTO {
  input_dataset_urn: string;
  job_id?: string;
  status?: string;
  error?: { code?: string; message?: string };
}

/** schedule_payload (inference-service schemas.py). `stage_selector` is the
 * stage NAME; next_fire_preview.at is the next computed fire time (null when
 * paused). */
export interface InferenceScheduleDTO {
  id: string;
  name?: string;
  enabled?: boolean;
  paused_reason?: string | null;
  model_version_urn?: string | null;
  model_urn?: string | null;
  stage_selector?: string | null;
  input_selector?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  cron?: string | null;
  interval_seconds?: number | null;
  timezone?: string | null;
  overlap_policy?: string | number | null;
  consecutive_failures?: number | null;
  temporal_schedule_id?: string | null;
  notify_on_failure?: boolean;
  next_fire_preview?: { at?: string | null } | null;
}

/** POST /schedules body (ScheduleBody). Server validation: exactly ONE of
 * model_version_urn/model_urn (model_urn additionally requires stage_selector),
 * and exactly ONE of cron/interval_seconds. */
export interface CreateInferenceScheduleBody {
  name: string;
  input_selector: Record<string, unknown>;
  output: Record<string, unknown>;
  model_version_urn?: string;
  model_urn?: string;
  stage_selector?: string;
  cron?: string;
  interval_seconds?: number;
  timezone?: string;
  overlap_policy?: string;
  enabled?: boolean;
  notify_on_failure?: boolean;
}

/** PATCH /schedules/{id} body (SchedulePatch) — ONLY these fields are
 * patchable; name/model/stage/enabled are not (pause/resume flip enabled). */
export interface PatchInferenceScheduleBody {
  cron?: string;
  interval_seconds?: number;
  timezone?: string;
  overlap_policy?: string;
  input_selector?: Record<string, unknown>;
  output?: Record<string, unknown>;
  notify_on_failure?: boolean;
}

/** POST /schedules/{id}/trigger `data` (ScheduleService.fire, forced=true):
 * {fired: true, job_id, status(int)} or {fired: false, reason, error?}. */
export interface ScheduleTriggerResultDTO {
  fired: boolean;
  job_id?: string;
  status?: number;
  reason?: string;
  error?: string;
}

export class InferenceClient {
  constructor(private readonly http: ServiceClient) {}

  jobs(p: InferenceListParams): Promise<Page<InferenceJobDTO>> {
    return this.http.get<Page<InferenceJobDTO>>("/api/v1/inferences", {
      query: { "filter[status]": p.status, limit: p.limit, cursor: p.cursor },
    });
  }

  async job(id: string): Promise<InferenceJobDTO> {
    const r = await this.http.get<{ data: InferenceJobDTO } | InferenceJobDTO>(
      `/api/v1/inferences/${encodeURIComponent(id)}`,
    );
    return unwrap<InferenceJobDTO>(r);
  }

  /**
   * POST /inferences submits a job (202, body {operation_id, job_id, status}) and
   * then GETs the created job so the resolver returns a full row. A rejected submit
   * answers 422 (compatibility failure) → DownstreamError bubbles as a real error;
   * we never fabricate a job (END STATE honesty).
   */
  async createJob(body: CreateInferenceBody, idempotencyKey?: string): Promise<InferenceJobDTO> {
    const r = await this.http.post<{ data: SubmitResponse } | SubmitResponse>("/api/v1/inferences", {
      body,
      idempotencyKey,
    });
    const submit = unwrap<SubmitResponse>(r);
    if (!submit.job_id) {
      throw new Error("inference submit returned no job_id");
    }
    return this.job(submit.job_id);
  }

  // ==== Tier 4b: ml ops ======================================================

  /** POST /inferences/{id}/cancel — 200 with the updated job. Idempotent no-op
   * when already cancelled/cancelling; a 409 for any other non-cancellable
   * state bubbles verbatim. Needs inference.job.update. */
  async cancelJob(id: string): Promise<InferenceJobDTO> {
    const r = await this.http.post<{ data: InferenceJobDTO } | InferenceJobDTO>(
      `/api/v1/inferences/${encodeURIComponent(id)}/cancel`,
    );
    return unwrap<InferenceJobDTO>(r);
  }

  /** POST /inferences/{id}/retry (202, {operation_id, job_id}) resubmits a
   * TERMINAL-FAILURE job (rejected|failed|cancelled → 409 otherwise) as a NEW
   * job, then GETs that new job so the resolver returns a full row (same
   * pattern as createJob). Needs inference.job.create. */
  async retryJob(id: string, idempotencyKey?: string): Promise<InferenceJobDTO> {
    const r = await this.http.post<{ data: SubmitResponse } | SubmitResponse>(
      `/api/v1/inferences/${encodeURIComponent(id)}/retry`,
      { idempotencyKey },
    );
    const retried = unwrap<SubmitResponse>(r);
    if (!retried.job_id) {
      throw new Error("inference retry returned no job_id");
    }
    return this.job(retried.job_id);
  }

  /** DELETE /inferences/{id} — 204; terminal jobs only (409 otherwise). Needs
   * inference.job.delete. */
  async deleteJob(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/inferences/${encodeURIComponent(id)}`);
  }

  /** POST /inferences/validate — the standalone model×dataset compatibility
   * check (stage policy folded into the verdict as stage_error). Read-only;
   * needs inference.job.read. */
  async validate(body: ValidateInferenceBody): Promise<CompatibilityReportDTO> {
    const r = await this.http.post<{ data: CompatibilityReportDTO } | CompatibilityReportDTO>(
      "/api/v1/inferences/validate",
      { body },
    );
    return unwrap<CompatibilityReportDTO>(r);
  }

  /** POST /inferences/bulk — one model over up to 20 datasets; the response is
   * the REAL per-dataset partial-failure list. NB: `unwrap` passes list
   * envelopes through, so the array is read off `data` directly. Needs
   * inference.job.create. */
  async bulkCreate(body: BulkInferenceBody): Promise<BulkInferenceResultDTO[]> {
    const r = await this.http.post<{ data?: BulkInferenceResultDTO[] }>("/api/v1/inferences/bulk", {
      body,
    });
    return r?.data ?? [];
  }

  // ---- scheduled scoring (INF-FR-050..055) ----------------------------------

  /** GET /schedules — cursor-paginated. Needs inference.schedule.read. */
  schedules(limit: number, cursor?: string): Promise<Page<InferenceScheduleDTO>> {
    return this.http.get<Page<InferenceScheduleDTO>>("/api/v1/schedules", {
      query: { limit, cursor },
    });
  }

  /** GET /schedules/{id}. Needs inference.schedule.read. */
  async schedule(id: string): Promise<InferenceScheduleDTO> {
    const r = await this.http.get<{ data: InferenceScheduleDTO } | InferenceScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}`,
    );
    return unwrap<InferenceScheduleDTO>(r);
  }

  /** POST /schedules (201). Duplicate names 409; timing/model XOR violations
   * 422 — all verbatim. Needs inference.schedule.create. */
  async createSchedule(body: CreateInferenceScheduleBody): Promise<InferenceScheduleDTO> {
    const r = await this.http.post<{ data: InferenceScheduleDTO } | InferenceScheduleDTO>(
      "/api/v1/schedules",
      { body },
    );
    return unwrap<InferenceScheduleDTO>(r);
  }

  /** PATCH /schedules/{id} — timing/overlap/selectors/notify only. Needs
   * inference.schedule.update. */
  async patchSchedule(id: string, body: PatchInferenceScheduleBody): Promise<InferenceScheduleDTO> {
    const r = await this.http.patch<{ data: InferenceScheduleDTO } | InferenceScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<InferenceScheduleDTO>(r);
  }

  /** DELETE /schedules/{id} — 204 (soft delete + disarm). Needs
   * inference.schedule.delete. */
  async deleteSchedule(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/schedules/${encodeURIComponent(id)}`);
  }

  /** POST /schedules/{id}/pause — disables + clears next fire. Needs
   * inference.schedule.update. */
  async pauseSchedule(id: string): Promise<InferenceScheduleDTO> {
    const r = await this.http.post<{ data: InferenceScheduleDTO } | InferenceScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}/pause`,
    );
    return unwrap<InferenceScheduleDTO>(r);
  }

  /** POST /schedules/{id}/resume — re-enables + resets the failure breaker.
   * Needs inference.schedule.update. */
  async resumeSchedule(id: string): Promise<InferenceScheduleDTO> {
    const r = await this.http.post<{ data: InferenceScheduleDTO } | InferenceScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}/resume`,
    );
    return unwrap<InferenceScheduleDTO>(r);
  }

  /** POST /schedules/{id}/trigger (202) — one forced fire, bypassing the
   * overlap policy; answers the real fire result. Needs inference.schedule.update. */
  async triggerSchedule(id: string): Promise<ScheduleTriggerResultDTO> {
    const r = await this.http.post<{ data: ScheduleTriggerResultDTO } | ScheduleTriggerResultDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}/trigger`,
    );
    return unwrap<ScheduleTriggerResultDTO>(r);
  }

  /** GET /schedules/{id}/fires — the schedule's job history (job_payload rows,
   * newest first). Needs inference.schedule.read. */
  scheduleFires(id: string, limit: number, cursor?: string): Promise<Page<InferenceJobDTO>> {
    return this.http.get<Page<InferenceJobDTO>>(
      `/api/v1/schedules/${encodeURIComponent(id)}/fires`,
      { query: { limit, cursor } },
    );
  }
}

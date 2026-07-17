/** experiment-service REST client (BRD 10). Backs: Experiment, Run, RegisteredModel. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface ExperimentDTO {
  id: string;
  name?: string;
  description?: string;
  tags?: string[];
  created_at?: string;
  /** _experiment_payload: `exp.deleted_at is not None` — archive marker, same
   * deleted_at-derived pattern as dataset-service (no separate status value). */
  archived?: boolean;
}

export interface RunDTO {
  id: string;
  experiment_id?: string;
  name?: string;
  status?: string;
  metrics?: MetricsDTO;
  params?: Record<string, string> | { key: string; value: string }[];
  model_id?: string;
}

/** One logged metric point (run detail serializes the LAST point per key). */
export interface MetricPointDTO {
  value?: number;
  step?: number;
  logged_at?: string;
}

/** Metrics arrive keyed by name -> last point ({value,step,logged_at}) on the
 * detail path; plain name -> number and [{key,value}] shapes are kept for
 * defensiveness against older payloads. */
export type MetricsDTO =
  | Record<string, number | MetricPointDTO>
  | { key: string; value: number }[];

/**
 * GET /api/v1/runs/{id} detail body (inside the {data: ...} envelope): the run
 * fields live under `run`, with params/metrics/tags/artifacts as SIBLINGS —
 * not flat on the body (experiment-service services.py get_detail).
 */
export interface RunDetailDTO {
  run?: RunDTO;
  params?: Record<string, string>;
  params_conflict?: string[];
  metrics?: Record<string, MetricPointDTO>;
  tags?: Record<string, string>;
  artifacts?: unknown[];
  input_dataset_urns?: string[];
  output_dataset_urns?: string[];
  note?: string | null;
}

export interface ModelDTO {
  id: string;
  name?: string;
  versions?: unknown[];
  stage?: string;
}

/** A registered model header (experiment-service _model_payload). */
export interface RegistryModelDTO {
  id: string;
  urn?: string;
  name?: string;
  model_type?: string;
  owner_id?: string | null;
  description?: string | null;
  created_at?: string | null;
}

/** A model VERSION with its promotion stage (experiment-service _version_payload).
 * `stage` is the label: production | staging | archived | none. */
export interface ModelVersionDTO {
  model_id: string;
  version: number;
  urn?: string;
  source_run_id?: string | null;
  stage?: string;
  mlflow_model_ref?: string | null;
  flavor?: string | null;
  input_schema?: unknown;
  output_schema?: unknown;
  stage_updated_at?: string | null;
}

/** GET /models/{id} `data`: the model header + its full version list. */
export interface ModelDetailDTO {
  model: RegistryModelDTO;
  versions: ModelVersionDTO[];
}

/** POST /experiments body (experiment-service ExperimentCreate). All three
 * pipeline URNs are required and must be mutually distinct (EXP-FR-001). */
export interface CreateExperimentBody {
  workspace_id: string;
  name: string;
  model_type: string;
  model_pipeline_urn: string;
  feature_engineering_pipeline_urn: string;
  training_pipeline_urn: string;
  description?: string;
  tags?: Record<string, unknown>;
}

/** POST /models/{id}/versions/{v}/promote (202) `data`. */
export interface PromotionRequestDTO {
  operation_id?: string;
  promotion_id?: string;
  status?: string;
}

export interface ModelListParams {
  stage?: string;
  limit: number;
  cursor?: string;
}

// ==== Tier 4b: ml ops (register / best-run / compare / notes / artifacts /
// metric history / model cards) — DTOs mirror experiment-service payloads ====

/** POST /experiments/{eid}/runs/{rid}/register body (RegisterRequest). */
export interface RegisterRunBody {
  model_name: string;
  owner_id?: string;
  description?: string;
  /** Defaults server-side to "mlflow.sklearn". */
  flavor?: string;
  mlflow_model_ref?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
}

/** register response `data` (RegistryService.register): the new version +
 * whether the model header itself was created by this call. */
export interface RegisterRunResultDTO {
  model_id: string;
  version: number;
  /** Always "none" on a fresh registration. */
  stage?: string;
  model_created?: boolean;
}

/** GET /experiments/{id}/runs/best `data`: the full _run_payload PLUS
 * "metrics": {key: float} (QueryService.best_run). */
export interface BestRunDTO extends RunDTO {
  metrics?: Record<string, number>;
}

/** POST /runs/compare body (CompareRequest). */
export interface CompareRunsBody {
  run_ids: string[];
  metrics?: string[];
  params?: string[];
  include_all?: boolean;
}

/** One compare metric row (compare.py build_comparison): values is
 * {run_id: value|null} aligned to the requested run order. */
export interface CompareMetricRowDTO {
  key: string;
  values: Record<string, number | null>;
  best_run_id?: string | null;
  direction?: string;
}

/** One compare param row: `differs` flags any cross-run difference/absence. */
export interface CompareParamRowDTO {
  key: string;
  values: Record<string, string | null>;
  differs?: boolean;
}

/** POST /runs/compare response: {data: {runs, metrics, params}, page}. */
export interface CompareRunsResponseDTO {
  data: {
    runs: string[];
    metrics: CompareMetricRowDTO[];
    params: CompareParamRowDTO[];
  };
  page?: { next_cursor?: string | null; has_more?: boolean };
}

/** PATCH /experiments/{id} body (ExperimentPatch; exclude_unset semantics —
 * omit a field to leave it unchanged). */
export interface ExperimentPatchBody {
  name?: string;
  description?: string;
  note?: string;
  tags?: Record<string, unknown>;
}

/** Run note routes serialize {run_id, description} (GET 404s when none). */
export interface RunNoteDTO {
  run_id: string;
  description?: string | null;
}

/** One metric-history row (store metric_history): raw logged points. */
export interface MetricHistoryRowDTO {
  key: string;
  step?: number;
  value?: number;
  logged_at?: string;
}

/** GET /runs/{id}/artifacts `data` row. */
export interface RunArtifactDTO {
  path: string;
  size_bytes?: number | null;
  content_type?: string | null;
}

/** PATCH .../card body (CardPatch): the 4 human overlay fields. */
export interface ModelCardOverlayBody {
  intended_use?: string;
  limitations?: string;
  evaluation_summary?: string;
  ethical_considerations?: string;
}

/** GET /models/{id}/versions/{v}/promotions row (services.py _promotion_payload).
 * target_stage/from_stage/status are already human-readable labels (not the
 * numeric STAGE/PROMOTION_STATUS codes). No server-side status filter exists —
 * callers filter client-side (e.g. to "pending" for an approval queue). */
export interface PromotionDTO {
  id: string;
  urn?: string;
  model_version_id?: string;
  target_stage?: string;
  from_stage?: string;
  status?: string;
  rationale?: string | null;
  requested_by?: string | null;
  via_agent?: unknown;
  decision?: unknown;
  created_at?: string | null;
}

export class ExperimentClient {
  constructor(private readonly http: ServiceClient) {}

  experiments(limit: number, cursor?: string): Promise<Page<ExperimentDTO>> {
    return this.http.get<Page<ExperimentDTO>>("/api/v1/experiments", { query: { limit, cursor } });
  }

  /** GET /experiments/list_archived — a DEDICATED archived-only list route
   * (unlike dataset-service, which has no equivalent), needs experiment.experiment.read. */
  archivedExperiments(limit: number, cursor?: string, workspaceId?: string): Promise<Page<ExperimentDTO>> {
    return this.http.get<Page<ExperimentDTO>>("/api/v1/experiments/list_archived", {
      query: { limit, cursor, "filter[workspace_id]": workspaceId },
    });
  }

  /** DELETE /experiments/{id} — archive (sets deleted_at), 200 with the updated
   * experiment. Needs experiment.experiment.delete. */
  async archiveExperiment(id: string): Promise<ExperimentDTO> {
    const r = await this.http.delete<{ data: ExperimentDTO } | ExperimentDTO>(
      `/api/v1/experiments/${encodeURIComponent(id)}`,
    );
    return unwrap<ExperimentDTO>(r);
  }

  /** PATCH /experiments/{id}/restore — clears deleted_at. Needs experiment.experiment.update. */
  async restoreExperiment(id: string): Promise<ExperimentDTO> {
    const r = await this.http.patch<{ data: ExperimentDTO } | ExperimentDTO>(
      `/api/v1/experiments/${encodeURIComponent(id)}/restore`,
    );
    return unwrap<ExperimentDTO>(r);
  }

  async experiment(id: string): Promise<ExperimentDTO> {
    const r = await this.http.get<{ data: ExperimentDTO } | ExperimentDTO>(
      `/api/v1/experiments/${encodeURIComponent(id)}`,
    );
    return unwrap<ExperimentDTO>(r);
  }

  experimentRuns(experimentId: string, limit: number, cursor?: string): Promise<Page<RunDTO>> {
    return this.http.get<Page<RunDTO>>(
      `/api/v1/experiments/${encodeURIComponent(experimentId)}/runs`,
      { query: { limit, cursor } },
    );
  }

  /**
   * Batch runs across many experiments in ONE call (runsByExperimentId loader,
   * BFF-FR-030). Requires experiment-service to treat `filter[experiment_id]`
   * as a comma-separated IN list. Each run carries `experiment_id` for grouping.
   */
  async runsByExperimentIds(experimentIds: string[]): Promise<RunDTO[]> {
    const res = await this.http.get<Page<RunDTO>>("/api/v1/runs", {
      query: { "filter[experiment_id]": experimentIds.join(","), limit: 200 },
    });
    return res.data ?? [];
  }

  /** Batch models by id in ONE call (modelById loader): GET /models?filter[id]=… */
  async modelsByIds(ids: string[]): Promise<ModelDTO[]> {
    const res = await this.http.get<Page<ModelDTO>>("/api/v1/models", {
      query: { "filter[id]": ids.join(","), limit: ids.length },
    });
    return res.data ?? [];
  }

  /**
   * GET /runs/{id} answers {data: {run: {...}, params, metrics, artifacts, ...}}:
   * the run fields are nested under `run` with params/metrics as siblings.
   * Flatten to one RunDTO here so the mapper reads real values (params as-is;
   * metrics stay {key: {value,step,...}} — mapRun reduces to last value per key).
   */
  async run(id: string): Promise<RunDTO> {
    const r = await this.http.get<{ data: RunDetailDTO | RunDTO } | RunDetailDTO | RunDTO>(
      `/api/v1/runs/${encodeURIComponent(id)}`,
    );
    const body = unwrap<RunDetailDTO | RunDTO>(r);
    if (body && typeof body === "object" && "run" in body && body.run && typeof body.run === "object") {
      const detail = body as RunDetailDTO;
      return { ...detail.run!, params: detail.params, metrics: detail.metrics };
    }
    // Defensive: an already-flat run payload passes through untouched.
    return body as RunDTO;
  }

  async model(id: string): Promise<ModelDTO> {
    const r = await this.http.get<{ data: ModelDTO } | ModelDTO>(
      `/api/v1/models/${encodeURIComponent(id)}`,
    );
    return unwrap<ModelDTO>(r);
  }

  // ---- model registry: list + detail (versions + stages) --------------------

  /** GET /models — registered model headers (no versions), cursor-paginated. */
  models(p: ModelListParams): Promise<Page<RegistryModelDTO>> {
    return this.http.get<Page<RegistryModelDTO>>("/api/v1/models", {
      query: { "filter[stage]": p.stage, limit: p.limit, cursor: p.cursor },
    });
  }

  /** GET /models/{id} — the model header + its full version list (with stages).
   * The body is {data: {model, versions}}. */
  async modelDetail(id: string): Promise<ModelDetailDTO> {
    const r = await this.http.get<{ data: ModelDetailDTO } | ModelDetailDTO>(
      `/api/v1/models/${encodeURIComponent(id)}`,
    );
    return unwrap<ModelDetailDTO>(r);
  }

  // ---- experiment create ----------------------------------------------------

  /** POST /experiments — create an experiment (201). workspace_id is sourced from
   * the caller's JWT claim by the resolver (the SDL input omits it). */
  async createExperiment(body: CreateExperimentBody, idempotencyKey?: string): Promise<ExperimentDTO> {
    const r = await this.http.post<{ data: ExperimentDTO } | ExperimentDTO>("/api/v1/experiments", {
      body,
      idempotencyKey,
    });
    return unwrap<ExperimentDTO>(r);
  }

  // ---- promotion (four-eyes) ------------------------------------------------

  /** POST /models/{id}/versions/{v}/promote — request a stage transition (202).
   * Needs experiment.model.update. Answers {operation_id, data:{promotion_id, status}}. */
  async promoteVersion(
    modelId: string,
    version: number,
    body: { target_stage: string; rationale?: string },
    idempotencyKey?: string,
  ): Promise<PromotionRequestDTO> {
    const r = await this.http.post<{ operation_id?: string; data: PromotionRequestDTO } | PromotionRequestDTO>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${version}/promote`,
      { body, idempotencyKey },
    );
    const operationId = (r as { operation_id?: string }).operation_id;
    const data = unwrap<PromotionRequestDTO>(r);
    return { operation_id: data.operation_id ?? operationId, ...data };
  }

  /** POST /promotions/{id}/decision — approve/reject a pending promotion.
   * Needs experiment.promotion.approve; the service forbids self-approval (four-eyes). */
  async decidePromotion(
    promotionId: string,
    body: { decision: string; message?: string; target_stage?: string },
  ): Promise<Record<string, unknown>> {
    const r = await this.http.post<{ data: Record<string, unknown> } | Record<string, unknown>>(
      `/api/v1/promotions/${encodeURIComponent(promotionId)}/decision`,
      { body },
    );
    return unwrap<Record<string, unknown>>(r);
  }

  /** GET /models/{id}/versions/{v}/promotions — promotion history for one model
   * version (the approval-queue source). Needs experiment.model.read. */
  promotions(modelId: string, version: number, limit: number, cursor?: string): Promise<Page<PromotionDTO>> {
    return this.http.get<Page<PromotionDTO>>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${version}/promotions`,
      { query: { limit, cursor } },
    );
  }

  // ==== Tier 4b: ml ops ======================================================

  /** POST /experiments/{eid}/runs/{rid}/register (201, idempotent) — register a
   * FINISHED run as a model version. Needs experiment.model.create. A not-yet-
   * finished run answers RunNotFinished; a name registered under a different
   * model_type answers ModelTypeMismatch — both bubble verbatim. */
  async registerRun(
    experimentId: string,
    runId: string,
    body: RegisterRunBody,
    idempotencyKey?: string,
  ): Promise<RegisterRunResultDTO> {
    const r = await this.http.post<{ data: RegisterRunResultDTO } | RegisterRunResultDTO>(
      `/api/v1/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/register`,
      { body, idempotencyKey },
    );
    return unwrap<RegisterRunResultDTO>(r);
  }

  /** GET /experiments/{id}/runs/best — the best run by one metric. `direction`
   * is max|min (the service 422s anything else); 404 when NO run in the
   * experiment carries the metric. Needs experiment.run.read. */
  async bestRun(
    experimentId: string,
    metric: string,
    direction?: string,
    status?: string,
  ): Promise<BestRunDTO> {
    const r = await this.http.get<{ data: BestRunDTO } | BestRunDTO>(
      `/api/v1/experiments/${encodeURIComponent(experimentId)}/runs/best`,
      { query: { metric, direction, status } },
    );
    return unwrap<BestRunDTO>(r);
  }

  /** POST /runs/compare — the server-side comparison matrix. Any non-visible
   * run id 404s the whole request (BR-9). Needs experiment.run.read. */
  compareRuns(body: CompareRunsBody, cursor?: string): Promise<CompareRunsResponseDTO> {
    return this.http.post<CompareRunsResponseDTO>("/api/v1/runs/compare", {
      body,
      query: { cursor },
    });
  }

  /** PATCH /experiments/{id} — name/description/note/tags (exclude_unset).
   * Needs experiment.experiment.update. */
  async patchExperiment(id: string, body: ExperimentPatchBody): Promise<ExperimentDTO> {
    const r = await this.http.patch<{ data: ExperimentDTO } | ExperimentDTO>(
      `/api/v1/experiments/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<ExperimentDTO>(r);
  }

  /** PUT /runs/{id}/note — upsert the run's note (PUT and POST share the same
   * upsert handler; PUT is used so repeats are idempotent). Needs experiment.run.update. */
  async upsertRunNote(runId: string, description: string): Promise<RunNoteDTO> {
    const r = await this.http.put<{ data: RunNoteDTO } | RunNoteDTO>(
      `/api/v1/runs/${encodeURIComponent(runId)}/note`,
      { body: { description } },
    );
    return unwrap<RunNoteDTO>(r);
  }

  /** GET /runs/{id}/note — 404 when the run has no note (the resolver maps that
   * to null). Needs experiment.run.read. */
  async runNote(runId: string): Promise<RunNoteDTO> {
    const r = await this.http.get<{ data: RunNoteDTO } | RunNoteDTO>(
      `/api/v1/runs/${encodeURIComponent(runId)}/note`,
    );
    return unwrap<RunNoteDTO>(r);
  }

  /** DELETE /runs/{id}/note — answers {run_id, note_deleted: true}. Needs
   * experiment.run.update. */
  async deleteRunNote(runId: string): Promise<{ run_id: string; note_deleted?: boolean }> {
    const r = await this.http.delete<
      { data: { run_id: string; note_deleted?: boolean } } | { run_id: string; note_deleted?: boolean }
    >(`/api/v1/runs/${encodeURIComponent(runId)}/note`);
    return unwrap<{ run_id: string; note_deleted?: boolean }>(r);
  }

  /** GET /runs/{id}/metric-history — raw logged metric points ({key, step,
   * value, logged_at}), cursor-paginated, optional key filter (csv). Needs
   * experiment.run.read. */
  metricHistory(
    runId: string,
    keys?: string[],
    limit = 200,
    cursor?: string,
  ): Promise<Page<MetricHistoryRowDTO>> {
    return this.http.get<Page<MetricHistoryRowDTO>>(
      `/api/v1/runs/${encodeURIComponent(runId)}/metric-history`,
      { query: { keys: keys && keys.length > 0 ? keys.join(",") : undefined, limit, cursor } },
    );
  }

  /** GET /runs/{id}/artifacts — {data: [{path, size_bytes, content_type}]}.
   * NB: `unwrap` deliberately passes list envelopes through, so the array is
   * read off `data` directly here. Needs experiment.run.read. */
  async runArtifacts(runId: string): Promise<RunArtifactDTO[]> {
    const r = await this.http.get<{ data?: RunArtifactDTO[] }>(
      `/api/v1/runs/${encodeURIComponent(runId)}/artifacts`,
    );
    return r?.data ?? [];
  }

  /** GET /runs/{id}/artifacts/url?path= — a REAL signed url for one artifact
   * (404 when the path is not among the run's artifacts). Needs experiment.run.read. */
  async runArtifactUrl(runId: string, path: string): Promise<{ url: string; path: string }> {
    const r = await this.http.get<{ data: { url: string; path: string } } | { url: string; path: string }>(
      `/api/v1/runs/${encodeURIComponent(runId)}/artifacts/url`,
      { query: { path } },
    );
    return unwrap<{ url: string; path: string }>(r);
  }

  /** GET /models/{id}/versions/{v}/card — the MERGED model card (auto fields +
   * human overlay) as JSON (the markdown format mode is not used here). 404
   * when model/version/card is missing. Needs experiment.model.read. */
  async modelCard(modelId: string, version: number): Promise<Record<string, unknown>> {
    const r = await this.http.get<{ data: Record<string, unknown> } | Record<string, unknown>>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${version}/card`,
    );
    return unwrap<Record<string, unknown>>(r);
  }

  /** PATCH .../card — update any subset of the 4 overlay fields; answers the
   * full merged card. Needs experiment.model_card.update. */
  async patchModelCard(
    modelId: string,
    version: number,
    body: ModelCardOverlayBody,
  ): Promise<Record<string, unknown>> {
    const r = await this.http.patch<{ data: Record<string, unknown> } | Record<string, unknown>>(
      `/api/v1/models/${encodeURIComponent(modelId)}/versions/${version}/card`,
      { body },
    );
    return unwrap<Record<string, unknown>>(r);
  }
}

/** ingestion-service REST client (BRD 03). Backs: ConnectorType, DataConnection.
 *
 * Pure passthrough — the caller's JWT is forwarded verbatim by ServiceClient and
 * ingestion-service enforces every `ingestion.connection.*` action guard. The BFF
 * makes no authz/business decision here (BFF-FR-003/010/011). */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface ConnectorFieldDTO {
  name: string;
  type: string; // string | integer | number | boolean | enum | object | array
  required: boolean;
  secret: boolean;
  default?: unknown;
  enum?: unknown[] | null;
  help?: string | null;
}

export interface ConnectorTypeDTO {
  connector_type: string;
  display_name: string;
  category: string; // database | warehouse | object-store | file | saas
  fields: ConnectorFieldDTO[];
  secret_fields: string[];
  config_schema: Record<string, unknown>;
}

export interface ConnectionDTO {
  id: string;
  name: string;
  connector_type: string;
  config: Record<string, unknown>;
  secrets?: Record<string, string>;
  secret_set?: boolean;
  traffic_direction?: string;
  tags?: string[];
  workspace_id?: string;
  last_test_status?: string | null;
  last_tested_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ConnectionTestDTO {
  status: string; // "ok" | "failed"
  latency_ms?: number | null;
  error_category?: string | null;
  error_detail?: string | null;
}

/** A decision write-back job (ingestion-service GET /writebacks{,/{id}}, INS-FR-061).
 * status: pending_approval | delivering | delivered | failed | rejected. */
export interface WritebackDTO {
  id: string;
  connection_id: string;
  workspace_id?: string | null;
  decision_kind: string;
  decision_ref: string;
  idempotency_key: string;
  target: Record<string, unknown>;
  payload: Record<string, unknown>;
  status: string;
  approval_mode: string;
  requested_by: string;
  approved_by?: string | null;
  attempts: number;
  last_error?: string | null;
  target_ref?: string | null;
  delivered_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CreateWritebackBody {
  connection_id: string;
  decision_kind: string;
  decision_ref: string;
  idempotency_key: string;
  target?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  workspace_id?: string;
}

export interface ConnectionListParams {
  q?: string;
  connectorType?: string;
  trafficDirection?: string;
  limit: number;
  cursor?: string;
}

export interface CreateConnectionBody {
  name: string;
  connector_type: string;
  config: Record<string, unknown>;
  secrets?: Record<string, unknown>;
  traffic_direction?: string;
  tags?: string[];
  workspace_id?: string;
  skip_test?: boolean;
}

/** An ingestion run (ingestion-service GET /ingestions/{id}). */
export interface IngestionDTO {
  id: string;
  ingestion_mode: string;
  status: string;
  trigger?: string | null;
  connection_id?: string | null;
  dataset_urn?: string | null;
  new_dataset?: unknown;
  file_format?: string | null;
  statement?: string | null;
  schedule_id?: string | null;
  scheduled_for?: string | null;
  bytes_total?: number | null;
  bytes_received?: number | null;
  rows_appended?: number | null;
  attempts?: number | null;
  error_log?: unknown;
  workspace_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface IngestionListParams {
  status?: string;
  datasetUrn?: string;
  ingestionMode?: string;
  limit: number;
  cursor?: string;
}

export interface CreateIngestionBody {
  ingestion_mode: "file_upload" | "query" | "scheduled_run" | "webhook_batch";
  connection_id?: string;
  statement?: string;
  file_format?: string;
  dataset_urn?: string;
  new_dataset?: { name: string; description?: string } | Record<string, unknown>;
  skip_profiling?: boolean;
  allow_empty?: boolean;
  workspace_id?: string;
}

/** A recurring ingestion schedule (schedules.py serialize_schedule, ING-FR-060). */
export interface ScheduleDTO {
  id: string;
  connection_id: string;
  ingestion_template?: Record<string, unknown>;
  cron?: string | null;
  interval_seconds?: number | null;
  timezone?: string;
  watermark?: { column?: string; operator?: string; value_type?: string; current_value?: unknown } | null;
  overlap_policy?: string;
  enabled?: boolean;
  temporal_schedule_id?: string | null;
  workspace_id?: string;
  last_fired_at?: string | null;
  next_fire_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** POST /schedules body (schemas.ScheduleCreate; exactly ONE of cron /
 * interval_seconds — the service 422s otherwise). */
export interface CreateScheduleBody {
  connection_id: string;
  ingestion_template: Record<string, unknown>;
  cron?: string;
  interval_seconds?: number;
  timezone?: string;
  watermark?: { column: string; operator?: string; value_type?: string; initial_value: string };
  overlap_policy?: "skip" | "buffer_one";
  enabled?: boolean;
  workspace_id?: string;
}

/** PATCH /schedules/{id} body (schemas.ScheduleUpdate). */
export interface UpdateScheduleBody {
  cron?: string;
  interval_seconds?: number;
  timezone?: string;
  ingestion_template?: Record<string, unknown>;
  overlap_policy?: "skip" | "buffer_one";
  enabled?: boolean;
}

/** POST /schedules/{id}/run_now result (SchedulerService.fire): either
 * {skipped:true} (overlap policy) or {skipped:false, ingestion_id, buffered?}
 * possibly merged with the inline runner's result. */
export interface ScheduleFireDTO {
  skipped?: boolean;
  ingestion_id?: string;
  buffered?: boolean;
  status?: string;
  [k: string]: unknown;
}

/** PATCH /connections/{id} body (schemas.ConnectionUpdate). Secrets are
 * WRITE-ONLY: supplied keys are merged over the vault contents (partial
 * rotation); omitted keys are preserved; reads only ever return masks. */
export interface UpdateConnectionBody {
  name?: string;
  config?: Record<string, unknown>;
  secrets?: Record<string, unknown>;
  traffic_direction?: "incoming" | "outgoing" | "both";
  tags?: string[];
  skip_test?: boolean;
}

/** POST /connections/{id}/preview result (ING-FR-005: ≤100 rows, never persisted). */
export interface ConnectionPreviewDTO {
  columns?: string[];
  rows?: Record<string, unknown>[];
}

/** One confirmed part of a resumable upload session (uploads.py serialize_upload). */
export interface UploadPartDTO {
  n: number;
  etag: string;
  size: number;
}

/** A resumable chunked-upload session (ingestion-service GET/POST /uploads).
 * The actual chunk PUT bodies are raw binary and never pass through this BFF —
 * only session lifecycle (create/status/complete) is JSON and goes here. */
export interface UploadDTO {
  upload_id: string;
  ingestion_id?: string;
  status?: string;
  part_size?: number;
  bytes_total?: number | null;
  sha256?: string | null;
  expires_at?: string | null;
  parts?: UploadPartDTO[];
}

/** POST /uploads body (ingestion-service UploadCreate). */
export interface CreateUploadBody {
  ingestion_id: string;
  part_size?: number;
  bytes_total?: number;
}

/** POST /uploads/{id}/complete body (ingestion-service UploadComplete). */
export interface CompleteUploadBody {
  parts: { n: number; etag: string; size: number }[];
  sha256?: string;
}

export class IngestionClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /connector-types — the full catalog the UI renders per-type forms from. */
  async connectorTypes(): Promise<ConnectorTypeDTO[]> {
    const r = await this.http.get<{ data: ConnectorTypeDTO[] }>("/api/v1/connector-types");
    return r.data ?? [];
  }

  connections(p: ConnectionListParams): Promise<Page<ConnectionDTO>> {
    return this.http.get<Page<ConnectionDTO>>("/api/v1/connections", {
      query: {
        "filter[q]": p.q,
        "filter[connector_type]": p.connectorType,
        "filter[traffic_direction]": p.trafficDirection,
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  async connection(id: string): Promise<ConnectionDTO> {
    const r = await this.http.get<{ data: ConnectionDTO } | ConnectionDTO>(
      `/api/v1/connections/${encodeURIComponent(id)}`,
    );
    return unwrap<ConnectionDTO>(r);
  }

  /** POST /connections — create (probes on the server unless skip_test). */
  async createConnection(body: CreateConnectionBody, idempotencyKey?: string): Promise<ConnectionDTO> {
    const r = await this.http.post<{ data: ConnectionDTO } | ConnectionDTO>("/api/v1/connections", {
      body,
      idempotencyKey,
    });
    return unwrap<ConnectionDTO>(r);
  }

  /** POST /connections/{id}/test — probe a saved connection. */
  async testSaved(id: string): Promise<ConnectionTestDTO> {
    const r = await this.http.post<{ data: ConnectionTestDTO } | ConnectionTestDTO>(
      `/api/v1/connections/${encodeURIComponent(id)}/test`,
    );
    return unwrap<ConnectionTestDTO>(r);
  }

  /** POST /connections:test — probe an unsaved config (the create-flow Test button). */
  async testAdhoc(body: {
    connector_type: string;
    config: Record<string, unknown>;
    secrets?: Record<string, unknown>;
  }): Promise<ConnectionTestDTO> {
    const r = await this.http.post<{ data: ConnectionTestDTO } | ConnectionTestDTO>(
      "/api/v1/connections:test",
      { body },
    );
    return unwrap<ConnectionTestDTO>(r);
  }

  /** DELETE /connections/{id} — soft-delete (204). */
  async deleteConnection(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/connections/${encodeURIComponent(id)}`);
  }

  /** PATCH /connections/{id} — edit a saved connection (needs
   * ingestion.connection.update). Live-probes on config/secret change unless
   * skip_test. Secrets merge write-only (see UpdateConnectionBody). */
  async updateConnection(id: string, body: UpdateConnectionBody): Promise<ConnectionDTO> {
    const r = await this.http.patch<{ data: ConnectionDTO } | ConnectionDTO>(
      `/api/v1/connections/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<ConnectionDTO>(r);
  }

  /** POST /connections/{id}/preview — sample rows from a saved connection
   * before ingesting (ING-FR-005; needs ingestion.connection.read). Exactly one
   * of table/path/query is required. */
  async previewConnection(
    id: string,
    body: { table?: string; path?: string; query?: string; limit?: number },
  ): Promise<ConnectionPreviewDTO> {
    const r = await this.http.post<{ data: ConnectionPreviewDTO } | ConnectionPreviewDTO>(
      `/api/v1/connections/${encodeURIComponent(id)}/preview`,
      { body },
    );
    return unwrap<ConnectionPreviewDTO>(r);
  }

  /** GET /ingestions — ingestion runs, cursor-paginated. */
  ingestions(p: IngestionListParams): Promise<Page<IngestionDTO>> {
    return this.http.get<Page<IngestionDTO>>("/api/v1/ingestions", {
      query: {
        "filter[status]": p.status,
        "filter[dataset_urn]": p.datasetUrn,
        "filter[ingestion_mode]": p.ingestionMode,
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  /** GET /ingestions/{id}. */
  async ingestion(id: string): Promise<IngestionDTO> {
    const r = await this.http.get<{ data: IngestionDTO } | IngestionDTO>(
      `/api/v1/ingestions/${encodeURIComponent(id)}`,
    );
    return unwrap<IngestionDTO>(r);
  }

  /** POST /ingestions — create + kick off an ingestion run. */
  async createIngestion(body: CreateIngestionBody, idempotencyKey?: string): Promise<IngestionDTO> {
    const r = await this.http.post<{ data: IngestionDTO } | IngestionDTO>("/api/v1/ingestions", {
      body,
      idempotencyKey,
    });
    return unwrap<IngestionDTO>(r);
  }

  // ---- ingestion lifecycle (ING-FR-027/028/081) --------------------------------

  /** POST /ingestions/{id}/cancel — cancel an uncommitted run (409 once
   * committed/terminal; needs ingestion.ingestion.execute). */
  async cancelIngestion(id: string): Promise<IngestionDTO> {
    const r = await this.http.post<{ data: IngestionDTO } | IngestionDTO>(
      `/api/v1/ingestions/${encodeURIComponent(id)}/cancel`,
    );
    return unwrap<IngestionDTO>(r);
  }

  /** POST /ingestions/{id}/retry — clone a FAILED run into a fresh queued job
   * (202; 409 for any other status; needs ingestion.ingestion.execute). */
  async retryIngestion(id: string): Promise<IngestionDTO> {
    const r = await this.http.post<{ data: IngestionDTO } | IngestionDTO>(
      `/api/v1/ingestions/${encodeURIComponent(id)}/retry`,
    );
    return unwrap<IngestionDTO>(r);
  }

  /** POST /ingestions/{id}/reingest — re-run a TERMINAL job's config as a new
   * job (202; 409 while non-terminal; needs ingestion.ingestion.create). */
  async reingestIngestion(id: string): Promise<IngestionDTO> {
    const r = await this.http.post<{ data: IngestionDTO } | IngestionDTO>(
      `/api/v1/ingestions/${encodeURIComponent(id)}/reingest`,
    );
    return unwrap<IngestionDTO>(r);
  }

  // ---- recurring schedules (ING-FR-060..062) -----------------------------------

  /** GET /schedules — cursor-paginated. */
  schedules(p: { limit: number; cursor?: string }): Promise<Page<ScheduleDTO>> {
    return this.http.get<Page<ScheduleDTO>>("/api/v1/schedules", {
      query: { limit: p.limit, cursor: p.cursor },
    });
  }

  /** GET /schedules/{id}. */
  async schedule(id: string): Promise<ScheduleDTO> {
    const r = await this.http.get<{ data: ScheduleDTO } | ScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}`,
    );
    return unwrap<ScheduleDTO>(r);
  }

  /** POST /schedules — create (201; needs ingestion.schedule.create). */
  async createSchedule(body: CreateScheduleBody, idempotencyKey?: string): Promise<ScheduleDTO> {
    const r = await this.http.post<{ data: ScheduleDTO } | ScheduleDTO>("/api/v1/schedules", {
      body,
      idempotencyKey,
    });
    return unwrap<ScheduleDTO>(r);
  }

  /** PATCH /schedules/{id} (needs ingestion.schedule.update). */
  async updateSchedule(id: string, body: UpdateScheduleBody): Promise<ScheduleDTO> {
    const r = await this.http.patch<{ data: ScheduleDTO } | ScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}`,
      { body },
    );
    return unwrap<ScheduleDTO>(r);
  }

  /** DELETE /schedules/{id} — 204 (needs ingestion.schedule.delete). */
  async deleteSchedule(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/schedules/${encodeURIComponent(id)}`);
  }

  /** POST /schedules/{id}/pause — disable firing (needs ingestion.schedule.update). */
  async pauseSchedule(id: string): Promise<ScheduleDTO> {
    const r = await this.http.post<{ data: ScheduleDTO } | ScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}/pause`,
    );
    return unwrap<ScheduleDTO>(r);
  }

  /** POST /schedules/{id}/resume — re-enable firing (needs ingestion.schedule.update). */
  async resumeSchedule(id: string): Promise<ScheduleDTO> {
    const r = await this.http.post<{ data: ScheduleDTO } | ScheduleDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}/resume`,
    );
    return unwrap<ScheduleDTO>(r);
  }

  /** POST /schedules/{id}/run_now — force one fire (409 when disabled/deleted;
   * needs ingestion.schedule.execute). Deployments with inline execution run
   * the whole ingestion inside this call (dev; prod defers to Temporal), so it
   * gets a longer budget than the default 10s downstream cap. */
  async runScheduleNow(id: string): Promise<ScheduleFireDTO> {
    const r = await this.http.post<{ data: ScheduleFireDTO } | ScheduleFireDTO>(
      `/api/v1/schedules/${encodeURIComponent(id)}/run_now`,
      { timeoutMs: 60_000 },
    );
    return unwrap<ScheduleFireDTO>(r);
  }

  // ---- resumable uploads (ING-FR-040..042) -----------------------------------
  // Session lifecycle only — the binary PUT /uploads/{id}/parts/{n} chunk body
  // is NOT JSON and never goes through this client; the browser PUTs each chunk
  // directly to a ui-web API route that proxies to ingestion-service with the
  // caller's session forwarded (see services/ui-web/src/app/api/uploads).

  /** POST /uploads — create a session (201; needs ingestion.upload.create). */
  async createUpload(body: CreateUploadBody, idempotencyKey?: string): Promise<UploadDTO> {
    const r = await this.http.post<{ data: UploadDTO } | UploadDTO>("/api/v1/uploads", { body, idempotencyKey });
    return unwrap<UploadDTO>(r);
  }

  /** GET /uploads/{id} — status/progress (confirmed parts), needs ingestion.upload.read. */
  async upload(id: string): Promise<UploadDTO> {
    const r = await this.http.get<{ data: UploadDTO } | UploadDTO>(`/api/v1/uploads/${encodeURIComponent(id)}`);
    return unwrap<UploadDTO>(r);
  }

  /** POST /uploads/{id}/complete — finalize (202; needs ingestion.upload.execute).
   * Returns the serialized Ingestion (transitions to queued/running), not an Upload. */
  async completeUpload(id: string, body: CompleteUploadBody): Promise<IngestionDTO> {
    const r = await this.http.post<{ data: IngestionDTO } | IngestionDTO>(
      `/api/v1/uploads/${encodeURIComponent(id)}/complete`,
      { body },
    );
    return unwrap<IngestionDTO>(r);
  }

  // ---- decision write-back / SoR sync (INS-FR-061) ---------------------------
  // Governed, proposal-mode delivery of a platform decision to a tenant's own
  // system of record over an `outgoing` connection. Every job is four-eyes:
  // the approver must be a different principal than the requester (enforced
  // server-side, not just in the UI).

  /** GET /writebacks — newest first, no cursor (a bounded admin/ops list, not
   * an infinite-scroll surface). */
  async writebacks(p: { status?: string; workspaceId?: string; limit: number }): Promise<WritebackDTO[]> {
    const r = await this.http.get<{ data: WritebackDTO[] } | WritebackDTO[]>("/api/v1/writebacks", {
      query: { status: p.status, "filter[workspace_id]": p.workspaceId, limit: p.limit },
    });
    return Array.isArray(r) ? r : (r.data ?? []);
  }

  /** GET /writebacks/{id}. */
  async writeback(id: string): Promise<WritebackDTO> {
    const r = await this.http.get<{ data: WritebackDTO } | WritebackDTO>(
      `/api/v1/writebacks/${encodeURIComponent(id)}`,
    );
    return unwrap<WritebackDTO>(r);
  }

  /** POST /writebacks — enqueue (201; needs ingestion.writeback.create).
   * approval_mode is always server-forced to four_eyes regardless of what's
   * sent — the requester may not self-select auto-delivery (INS-FR-061). */
  async createWriteback(body: CreateWritebackBody, idempotencyKey?: string): Promise<WritebackDTO> {
    const r = await this.http.post<{ data: WritebackDTO } | WritebackDTO>("/api/v1/writebacks", {
      body, idempotencyKey,
    });
    return unwrap<WritebackDTO>(r);
  }

  /** POST /writebacks/{id}/approve — 422s server-side if the caller is the
   * same principal as requested_by (four-eyes; needs ingestion.writeback.approve). */
  async approveWriteback(id: string): Promise<WritebackDTO> {
    const r = await this.http.post<{ data: WritebackDTO } | WritebackDTO>(
      `/api/v1/writebacks/${encodeURIComponent(id)}/approve`,
    );
    return unwrap<WritebackDTO>(r);
  }

  /** POST /writebacks/{id}/reject (needs ingestion.writeback.approve — same
   * gate as approve, since rejecting is also a four-eyes governance action). */
  async rejectWriteback(id: string): Promise<WritebackDTO> {
    const r = await this.http.post<{ data: WritebackDTO } | WritebackDTO>(
      `/api/v1/writebacks/${encodeURIComponent(id)}/reject`,
    );
    return unwrap<WritebackDTO>(r);
  }

  /** POST /writebacks/{id}/retry — re-attempt a failed/stranded delivery
   * (needs ingestion.writeback.execute). */
  async retryWriteback(id: string): Promise<WritebackDTO> {
    const r = await this.http.post<{ data: WritebackDTO } | WritebackDTO>(
      `/api/v1/writebacks/${encodeURIComponent(id)}/retry`,
    );
    return unwrap<WritebackDTO>(r);
  }
}

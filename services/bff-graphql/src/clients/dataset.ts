/** dataset-service REST client (BRD 04). Backs: Dataset, Profile. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface DatasetDTO {
  id: string;
  workspace_id?: string;
  name: string;
  description?: string;
  status?: string;
  lifecycle?: string;
  tags?: string[];
  row_count?: number;
  created_at?: string;
  updated_at?: string;
  /** Archive marker (dataset_payload). Soft-delete via DELETE /datasets/{id} sets
   * this; restore via POST /datasets/{id}/restore clears it. There is no
   * separate `status: "archived"` value — `status` is the processing lifecycle
   * (draft/processing/ready/failed), unrelated to archival. */
  deleted_at?: string | null;
  /** dataset_payload nests the row count under current_version.row_count on the
   * detail path (null on the list path, which serializes headers only). */
  current_version?: {
    version_no?: number;
    iceberg_snapshot_id?: string;
    row_count?: number | null;
    bytes?: number;
    breaking_change?: boolean;
    profile_status?: string;
  } | null;
}

/** One column entry in a profile's `columns` array (dataset-service profile
 * payload) — real inferred name/type/nullability from a completed profiling
 * run, used as the datasetSchema fallback when the version's `schema` map is
 * empty (see DatasetVersionDTO.schema doc). */
export interface ProfileColumnDTO {
  name: string;
  logical_type?: string | null;
  null_pct?: number | null;
  distinct_count?: number | null;
  quality_flags?: string[];
}

export interface ProfileDTO {
  dataset_id?: string;
  profile_id?: string;
  status?: string;
  version_no?: number;
  /** get_summary nests the counts under `table` ({row_count, column_count, bytes,
   * duplicate_row_pct}); flat row_count/column_count kept for defensiveness. */
  table?: {
    row_count?: number | null;
    column_count?: number | null;
    bytes?: number | null;
    duplicate_row_pct?: number | null;
  } | null;
  row_count?: number;
  column_count?: number;
  columns?: ProfileColumnDTO[];
  summary?: unknown;
  alerts?: unknown[];
  full_json_url?: string;
  html_report_url?: string;
}

/** GET /datasets/{id}/versions[/{version_no}] item (dataset-service
 * version_payload, app/api/schemas.py). `schema` is the authoritative
 * column map ({col_name: {type, nullable, tags[]}}) but is empty for any
 * dataset version registered before schema capture was wired up on ingest
 * (a real, pre-existing data-quality gap on this deployment — see the
 * semantic authoring feature's datasetSchema resolver, which falls back to
 * the profile's columns when this is empty rather than showing nothing). */
export interface DatasetVersionDTO {
  id: string;
  urn?: string;
  dataset_id: string;
  version_no: number;
  iceberg_snapshot_id?: number | string;
  schema?: Record<string, { type?: string; nullable?: boolean; tags?: string[] }>;
  schema_diff?: unknown;
  breaking_change?: boolean;
  row_count?: number | null;
  bytes?: number | null;
  produced_by_urn?: string | null;
  profile_status?: string;
  expired?: boolean;
  created_at?: string;
}

export interface DatasetListParams {
  q?: string;
  limit: number;
  cursor?: string;
  sort?: string;
  status?: string;
  tags?: string;
}

/** GET /datasets/{id}/consumers summary (services.consumers_summary — a
 * depth-3 downstream lineage rollup, DST-FR-04x). */
export interface DatasetConsumersDTO {
  downstream_edges?: number;
  by_service?: Record<string, number>;
  by_activity?: Record<string, number>;
  truncated?: boolean;
}

/** POST /datasets:similar ranked result row (similarity search). */
export interface SimilarDatasetDTO {
  id?: string;
  dataset_id?: string;
  urn?: string;
  name?: string;
  score?: number;
  [k: string]: unknown;
}

/** POST /datasets/{id}/versions/{n}/profile 202 body (async re-profile trigger). */
export interface DatasetRowsDTO {
  columns: string[];
  rows: (string | null)[][];
  total: number;
  filtered: number;
  offset: number;
  limit: number;
  truncated?: boolean;
}

export interface ReprofileDTO {
  operation_id?: string;
  profile_id?: string;
  status?: string;
}

/** Lineage graph query result (dataset-service GET /lineage). */
export interface LineageDTO {
  nodes?: { urn: string; kind?: string; name?: string; status?: string }[];
  edges?: { from_urn: string; to_urn: string; activity?: string; run_urn?: string | null; occurred_at?: string }[];
  truncated?: boolean;
}

export class DatasetClient {
  constructor(private readonly http: ServiceClient) {}

  async dataset(id: string): Promise<DatasetDTO> {
    const r = await this.http.get<{ data: DatasetDTO } | DatasetDTO>(
      `/api/v1/datasets/${encodeURIComponent(id)}`,
    );
    return unwrap<DatasetDTO>(r);
  }

  /** Batch hydration for datasetById loader: GET /datasets?filter[id]=… */
  async datasetsByIds(ids: string[]): Promise<DatasetDTO[]> {
    const res = await this.http.get<Page<DatasetDTO>>("/api/v1/datasets", {
      query: { "filter[id]": ids.join(","), limit: ids.length },
    });
    return res.data ?? [];
  }

  datasets(p: DatasetListParams): Promise<Page<DatasetDTO>> {
    return this.http.get<Page<DatasetDTO>>("/api/v1/datasets", {
      query: {
        q: p.q,
        limit: p.limit,
        cursor: p.cursor,
        sort: p.sort,
        "filter[status]": p.status,
        "filter[tags]": p.tags,
      },
    });
  }

  /** GET /datasets/{id}/profile (profileByDatasetId loader is keyed on dataset id). */
  async profile(datasetId: string): Promise<ProfileDTO> {
    const r = await this.http.get<{ data: ProfileDTO } | ProfileDTO>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/profile`,
    );
    return unwrap<ProfileDTO>(r);
  }

  /** GET /datasets/{id}/versions — newest-first (DatasetListParams-less; the
   * route takes no filters). Needs dataset.dataset.read. */
  async versions(datasetId: string, limit = 50, cursor?: string): Promise<Page<DatasetVersionDTO>> {
    return this.http.get<Page<DatasetVersionDTO>>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/versions`,
      { query: { limit, cursor } },
    );
  }

  /** GET /datasets/{id}/versions/{version_no}. */
  async version(datasetId: string, versionNo: number): Promise<DatasetVersionDTO> {
    const r = await this.http.get<{ data: DatasetVersionDTO } | DatasetVersionDTO>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/versions/${versionNo}`,
    );
    return unwrap<DatasetVersionDTO>(r);
  }

  /** GET /lineage?urn=… — upstream/downstream URN graph (DST-FR-040..043). */
  async lineage(urn: string, direction = "both", depth?: number): Promise<LineageDTO> {
    const r = await this.http.get<{ data: LineageDTO } | LineageDTO>("/api/v1/lineage", {
      query: { urn, direction, depth },
    });
    return unwrap<LineageDTO>(r);
  }

  /** DELETE /datasets/{id} — soft-delete (sets deleted_at), 200 with a small
   * summary (NOT 204). Needs dataset.dataset.delete. `force` skips the
   * downstream-consumer guard (dataset-service 409s without it when the
   * dataset has downstream lineage edges). */
  async archive(id: string, force?: boolean): Promise<{ id: string; deleted: boolean; consumers?: unknown }> {
    const r = await this.http.delete<{ data: { id: string; deleted: boolean; consumers?: unknown } }>(
      `/api/v1/datasets/${encodeURIComponent(id)}`,
      { query: force ? { force: true } : undefined },
    );
    return r.data;
  }

  /** PATCH /datasets/{id} — edit a dataset's name and/or description (both
   * optional; the backend enforces name-uniqueness-in-workspace, excluding self).
   * Needs dataset.dataset.update. Sent without If-Match (last-write-wins for the
   * catalog metadata edit). */
  async update(id: string, input: { name?: string; description?: string }): Promise<DatasetDTO> {
    const r = await this.http.patch<{ data: DatasetDTO } | DatasetDTO>(
      `/api/v1/datasets/${encodeURIComponent(id)}`,
      { body: input },
    );
    return unwrap<DatasetDTO>(r);
  }

  /** POST /datasets/{id}/restore — clears deleted_at (within the service's restore
   * window; renames to "Copy of X" on a name collision). Needs dataset.dataset.update. */
  async restore(id: string): Promise<DatasetDTO> {
    const r = await this.http.post<{ data: DatasetDTO } | DatasetDTO>(
      `/api/v1/datasets/${encodeURIComponent(id)}/restore`,
    );
    return unwrap<DatasetDTO>(r);
  }

  /** GET /datasets/{id}/consumers — downstream-consumer rollup (needs
   * dataset.dataset.read). */
  async consumers(id: string): Promise<DatasetConsumersDTO> {
    const r = await this.http.get<{ data: DatasetConsumersDTO } | DatasetConsumersDTO>(
      `/api/v1/datasets/${encodeURIComponent(id)}/consumers`,
    );
    return unwrap<DatasetConsumersDTO>(r);
  }

  /** POST /datasets:similar — similarity search by schema map and/or column
   * names (needs dataset.dataset.read). */
  async similar(body: {
    schema?: Record<string, unknown>;
    columns?: string[];
  }): Promise<SimilarDatasetDTO[]> {
    const r = await this.http.post<{ data: SimilarDatasetDTO[] }>("/api/v1/datasets:similar", {
      body,
    });
    return r.data ?? [];
  }

  /** GET /datasets/{id}/rows — paginated/sortable/filterable row browse
   * (needs dataset.dataset.read). Filters are repeated `filter=col:op:value`
   * query params (op ∈ eq|neq|contains|gt|gte|lt|lte). */
  async rows(
    id: string,
    args: {
      offset?: number;
      limit?: number;
      sort?: string | null;
      dir?: string | null;
      filters?: { col: string; op: string; value: string }[];
    },
  ): Promise<DatasetRowsDTO> {
    const query: Record<string, string | number | string[]> = {
      offset: args.offset ?? 0,
      limit: args.limit ?? 50,
    };
    if (args.sort) query.sort = args.sort;
    if (args.dir) query.dir = args.dir;
    if (args.filters?.length) {
      query.filter = args.filters.map((f) => `${f.col}:${f.op}:${f.value}`);
    }
    const r = await this.http.get<{ data: DatasetRowsDTO } | DatasetRowsDTO>(
      `/api/v1/datasets/${encodeURIComponent(id)}/rows`,
      { query },
    );
    return unwrap<DatasetRowsDTO>(r);
  }

  /** POST /datasets/{id}/versions/{n}/profile — manual re-profile trigger
   * (202 async; needs dataset.profile.execute). */
  async reprofile(id: string, versionNo: number, idempotencyKey?: string): Promise<ReprofileDTO> {
    const r = await this.http.post<{ data: ReprofileDTO } | ReprofileDTO>(
      `/api/v1/datasets/${encodeURIComponent(id)}/versions/${versionNo}/profile`,
      { idempotencyKey },
    );
    return unwrap<ReprofileDTO>(r);
  }
}

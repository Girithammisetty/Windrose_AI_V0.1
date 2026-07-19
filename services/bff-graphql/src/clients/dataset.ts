/** dataset-service REST client (BRD 04). Backs: Dataset, Profile. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

/** A domain ontology entity TYPE and its shape (dataset-service inc11 registry). */
export interface OntologyAttributeDTO {
  name: string;
  data_type?: string;
}
export interface OntologyRelationshipDTO {
  name: string;
  target: string;
  cardinality?: string;
}
export interface OntologyEntityDTO {
  id: string;
  entity_key: string;
  workspace_id: string;
  name: string;
  description: string;
  attributes: OntologyAttributeDTO[];
  relationships: OntologyRelationshipDTO[];
  created_at?: string | null;
}

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

// ---- BRD 56: entity resolution (steward surface) ----------------------------

/** One weighted scoring field in a resolution config. */
export interface ScoringFieldDTO {
  column: string;
  weight?: number;
}

/** Resolution config the steward runs (dataset-service ResolutionConfigIn). */
export interface ResolutionConfigDTO {
  entity_type?: string;
  deterministic_keys?: string[][];
  scoring_fields?: ScoringFieldDTO[];
  blocking_fields?: string[];
  auto_merge_threshold?: number;
  review_threshold?: number;
}

/** POST /datasets/{id}/entity-resolution response (persist=true). Carries the
 * run summary + the persisted run/config ids so the UI can drill into the
 * stored views. */
export interface ResolveEntitiesDTO {
  dataset_id: string;
  entity_type: string;
  record_count: number;
  resolved_entity_count: number;
  merged_cluster_count: number;
  review_candidate_count: number;
  run_id?: string;
  config_id?: string;
  config_version?: number;
}

/** A persisted resolution run header (GET /datasets/{id}/resolution-runs). */
export interface ResolutionRunDTO {
  run_id: string;
  dataset_id: string;
  config_id?: string | null;
  entity_type: string;
  record_count: number;
  resolved_entity_count: number;
  merged_cluster_count: number;
  review_candidate_count: number;
  status: string;
  created_by?: string | null;
  created_at?: string | null;
}

/** One member record folded into a resolved entity (lineage, AC-4). */
export interface ResolvedMemberDTO {
  member_pk: string;
  method?: string | null;
  evidence?: unknown;
}

/** A resolved-entity cluster + its member lineage (GET /resolution-runs/{id}). */
export interface ResolvedClusterDTO {
  resolved_entity_id: string;
  member_count: number;
  confidence?: number | null;
  method?: string | null;
  members?: ResolvedMemberDTO[];
}

export interface ResolutionRunDetailDTO extends ResolutionRunDTO {
  clusters?: ResolvedClusterDTO[];
}

/** A below-auto merge candidate a steward reviews (GET /resolution-runs/{id}/merge-candidates). */
export interface MergeCandidateDTO {
  id: string;
  run_id: string;
  dataset_id: string;
  entity_type: string;
  left_pk: string;
  right_pk: string;
  score?: number | null;
  evidence?: unknown;
  status: string;
  proposal_id?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  created_at?: string | null;
}

/** One golden-record attribute rollup for materialization. */
export interface MaterializeAttributeDTO {
  column: string;
  agg?: string;
}

/** POST /resolution-runs/{id}/materialize response. */
export interface MaterializeResolvedDTO {
  resolved_dataset_id: string;
  resolved_dataset_urn: string;
  name: string;
  row_count: number;
  columns: string[];
  version_no: number;
  iceberg_table: string;
}

export class DatasetClient {
  constructor(private readonly http: ServiceClient) {}

  // ---- BRD 56: entity resolution ------------------------------------------

  /** POST /datasets/{id}/entity-resolution — run + persist a resolution run
   * (needs dataset.entity.execute). Link layer only; never mutates the source. */
  async resolveEntities(
    datasetId: string,
    body: { pk_column: string; config: ResolutionConfigDTO; row_limit?: number },
    idempotencyKey?: string,
  ): Promise<ResolveEntitiesDTO> {
    const r = await this.http.post<{ data: ResolveEntitiesDTO } | ResolveEntitiesDTO>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/entity-resolution`,
      { body: { ...body, persist: true }, idempotencyKey },
    );
    return unwrap<ResolveEntitiesDTO>(r);
  }

  /** GET /datasets/{id}/resolution-runs — prior runs, newest first (needs
   * dataset.entity.read). */
  async resolutionRuns(datasetId: string, limit = 50): Promise<ResolutionRunDTO[]> {
    const r = await this.http.get<{ data: ResolutionRunDTO[] }>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/resolution-runs`,
      { query: { limit } },
    );
    return r.data ?? [];
  }

  /** GET /resolution-runs/{id} — a run's resolved clusters + member lineage
   * (needs dataset.entity.read). */
  async resolutionRun(runId: string): Promise<ResolutionRunDetailDTO> {
    const r = await this.http.get<{ data: ResolutionRunDetailDTO } | ResolutionRunDetailDTO>(
      `/api/v1/resolution-runs/${encodeURIComponent(runId)}`,
    );
    return unwrap<ResolutionRunDetailDTO>(r);
  }

  /** GET /resolution-runs/{id}/merge-candidates — the review queue for a run
   * (needs dataset.entity.read). */
  async mergeCandidates(runId: string, status?: string): Promise<MergeCandidateDTO[]> {
    const r = await this.http.get<{ data: MergeCandidateDTO[] }>(
      `/api/v1/resolution-runs/${encodeURIComponent(runId)}/merge-candidates`,
      { query: status ? { status } : undefined },
    );
    return r.data ?? [];
  }

  /** POST /resolution-runs/{id}/materialize — build the governed resolved-entity
   * dataset (golden records). Needs dataset.entity.execute. */
  async materializeResolved(
    runId: string,
    body: { name?: string; workspace_id?: string; attributes: MaterializeAttributeDTO[] },
    idempotencyKey?: string,
  ): Promise<MaterializeResolvedDTO> {
    const r = await this.http.post<{ data: MaterializeResolvedDTO } | MaterializeResolvedDTO>(
      `/api/v1/resolution-runs/${encodeURIComponent(runId)}/materialize`,
      { body, idempotencyKey },
    );
    return unwrap<MaterializeResolvedDTO>(r);
  }

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

  // ---- domain ontology: governed entity-TYPE registry (inc11) ---------------

  /** GET /ontology/entities — the workspace's entity types (needs
   * dataset.ontology.read). */
  async ontologyEntities(workspaceId?: string): Promise<OntologyEntityDTO[]> {
    const r = await this.http.get<{ data: OntologyEntityDTO[] }>(
      "/api/v1/ontology/entities",
      { query: workspaceId ? { "filter[workspace_id]": workspaceId } : undefined },
    );
    return r.data ?? [];
  }

  /** POST /ontology/entities — register one entity type (idempotent by
   * entity_key within the workspace; needs dataset.ontology.create). */
  async createOntologyEntity(body: {
    workspace_id: string;
    entity_key: string;
    name: string;
    description?: string;
    attributes?: OntologyAttributeDTO[];
    relationships?: OntologyRelationshipDTO[];
  }): Promise<OntologyEntityDTO> {
    const r = await this.http.post<{ data: OntologyEntityDTO } | OntologyEntityDTO>(
      "/api/v1/ontology/entities",
      { body },
    );
    return unwrap<OntologyEntityDTO>(r);
  }

  /** DELETE /ontology/entities/{key} (204; needs dataset.ontology.delete). */
  async deleteOntologyEntity(entityKey: string, workspaceId: string): Promise<boolean> {
    await this.http.delete<void>(
      `/api/v1/ontology/entities/${encodeURIComponent(entityKey)}`,
      { query: { "filter[workspace_id]": workspaceId } },
    );
    return true;
  }
}

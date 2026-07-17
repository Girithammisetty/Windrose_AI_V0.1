/**
 * memory-service REST client (MEM-FR-010/011/020/040/050/051/052). Backs:
 * memory browse/search, single-record view, right-to-be-forgotten erasure
 * (start + status poll), and tenant memory stats. Pure passthrough — the
 * caller's JWT is forwarded verbatim and memory-service enforces
 * memory.memory.read / memory.erasure.{create,read} / memory.stats.read.
 */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

/** memory-service _memory_view row. status: active|quarantined|expired|deleted.
 * scope: session|user|workspace|tenant. `merged_from`/`revalidate_at` are only
 * present on the single-record (full=True) GET, not the browse list. */
export interface MemoryRecordDTO {
  memory_id: string;
  scope: string;
  scope_ref: string;
  content: string;
  confidence?: number | null;
  status: string;
  tags: string[];
  provenance?: unknown;
  retrieval_count?: number;
  classifier_score?: number | null;
  ttl_expires_at?: string | null;
  merged_from?: string[] | null;
  revalidate_at?: string | null;
}

export interface BrowseMemoriesParams {
  scope?: string;
  scopeRef?: string;
  status?: string;
  tags?: string[];
  limit: number;
  cursor?: string;
}

/** admin-service ErasureRequest. status: received|running|verifying|completed|failed. */
export interface ErasureRequestDTO {
  operation_id: string;
  status: string;
  report?: Record<string, unknown> | null;
  completed_at?: string | null;
}

export class MemoryClient {
  constructor(private readonly http: ServiceClient) {}

  /** GET /memories — browse/search (the "what does the agent know about this
   * workspace" surface). Needs memory.memory.read. */
  browse(p: BrowseMemoriesParams): Promise<Page<MemoryRecordDTO>> {
    return this.http.get<Page<MemoryRecordDTO>>("/api/v1/memories", {
      query: {
        scope: p.scope,
        scope_ref: p.scopeRef,
        "filter[status]": p.status,
        "filter[tags]": p.tags?.join(","),
        limit: p.limit,
        cursor: p.cursor,
      },
    });
  }

  /** GET /memories/{id} — single record, full detail. Needs memory.memory.read. */
  async memory(id: string): Promise<MemoryRecordDTO> {
    const r = await this.http.get<{ data: MemoryRecordDTO } | MemoryRecordDTO>(
      `/api/v1/memories/${encodeURIComponent(id)}`,
    );
    return unwrap<MemoryRecordDTO>(r);
  }

  /** POST /erasure (202) — start a right-to-be-forgotten erasure for a subject
   * (default subject_type "user"). Needs memory.erasure.create. */
  async startErasure(subjectId: string, subjectType = "user"): Promise<ErasureRequestDTO> {
    const r = await this.http.post<{ data: ErasureRequestDTO } | ErasureRequestDTO>(
      "/api/v1/erasure",
      { body: { subject_type: subjectType, subject_id: subjectId } },
    );
    return unwrap<ErasureRequestDTO>(r);
  }

  /** GET /erasure/{request_id} — poll erasure status/report. Needs memory.erasure.read. */
  async erasure(requestId: string): Promise<ErasureRequestDTO> {
    const r = await this.http.get<{ data: ErasureRequestDTO } | ErasureRequestDTO>(
      `/api/v1/erasure/${encodeURIComponent(requestId)}`,
    );
    return unwrap<ErasureRequestDTO>(r);
  }

  /** GET /stats — tenant memory stats (opaque dict; passed through as JSON).
   * Needs memory.stats.read. */
  async stats(): Promise<Record<string, unknown>> {
    const r = await this.http.get<{ data: Record<string, unknown> } | Record<string, unknown>>(
      "/api/v1/stats",
    );
    return unwrap<Record<string, unknown>>(r);
  }
}

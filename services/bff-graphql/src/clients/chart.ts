/** chart-service REST client (BRD 07). Backs: Dashboard, Chart, ChartData, and
 * the no-code editor's authoring + catalog surface (ChartType, chartPreview).
 *
 * Pure passthrough — the caller's JWT is forwarded verbatim by ServiceClient and
 * chart-service enforces every `chart.*` action guard. The BFF makes no
 * authz/business decision here; it only reshapes the REST payloads (snake→camel)
 * for the UI (BFF-FR-003/010/011). */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface DashboardDTO {
  id: string;
  workspace_id?: string;
  /** chart-service dashboardView serializes the dashboard label as `name`;
   * older read paths referenced `title` (mapDashboard falls back name→title). */
  name?: string;
  title?: string;
  module?: string;
  description?: string;
  layout?: unknown;
  meta?: unknown;
  tags?: string[];
  status?: string;
  archived?: boolean;
  chart_ids?: string[];
  charts?: ChartDTO[];
  created_at?: string;
  updated_at?: string;
}

export interface ChartSourceDTO {
  position?: number;
  source_type?: string;
  source_urn?: string;
}

export interface ChartDTO {
  id: string;
  dashboard_id?: string;
  name?: string;
  description?: string;
  spec?: unknown;
  chart_type?: string;
  config?: unknown;
  display_meta?: unknown;
  sources?: ChartSourceDTO[];
  chart_version?: number;
  provenance?: unknown;
  version?: number;
}

export interface ChartDataDTO {
  chart_id?: string;
  rows?: unknown[];
  columns?: unknown[];
  /** {nodes, edges} object shape for network-family charts (chart-service Shape). */
  graph?: unknown;
  /** Resolved artifact blob for the metric/parameter (dataset/run) family
   * (chart-service ShapedResult.artifact). */
  artifact?: unknown;
  meta?: { cache?: unknown; [k: string]: unknown };
  error?: { code?: string; message?: string };
}

/** One entry in the chart-type catalog (GET /chart-types). */
export interface ChartTypeDTO {
  name: string;
  family: string; // axis | y_only | heatmap | network | grid | metric
  data_class?: string; // query | dataset | run
  config_schema?: Record<string, unknown>; // JSON Schema
  required_fields?: string[];
}

/** ShapedResult from POST /charts/preview and GET /charts/{id}/data. */
export interface ChartShapedDataDTO {
  chart_id?: string;
  chart_type?: string;
  chart_version?: number;
  aggregated?: boolean;
  columns?: unknown;
  rows?: unknown;
  graph?: unknown;
  artifact?: unknown;
  row_count?: number;
  truncated?: boolean;
  resolved_at?: string;
}

export interface CreateDashboardBody {
  name: string;
  module?: string;
  workspace_id: string;
  description?: string;
  layout?: unknown;
  meta?: unknown;
  tags?: string[];
}

export interface UpdateDashboardBody {
  name?: string;
  description?: string;
  layout?: unknown;
  meta?: unknown;
  tags?: string[];
}

export interface ChartSourceInputBody {
  position: number;
  source_type: string;
  source_urn: string;
}

export interface CreateChartBody {
  name: string;
  chart_type: string;
  description?: string;
  config: Record<string, unknown>;
  display_meta?: Record<string, unknown>;
  sources?: ChartSourceInputBody[];
}

export interface UpdateChartBody {
  name?: string;
  chart_type?: string;
  config?: Record<string, unknown>;
  display_meta?: Record<string, unknown>;
  sources?: ChartSourceInputBody[];
}

export interface PreviewChartBody {
  chart_type: string;
  config: Record<string, unknown>;
  display_meta?: Record<string, unknown>;
  sources?: ChartSourceInputBody[];
}

export class ChartClient {
  constructor(private readonly http: ServiceClient) {}

  // ---- read paths ----------------------------------------------------------
  async dashboard(id: string): Promise<DashboardDTO> {
    const r = await this.http.get<{ data: DashboardDTO } | DashboardDTO>(
      `/api/v1/dashboards/${encodeURIComponent(id)}`,
    );
    return unwrap<DashboardDTO>(r);
  }

  /** GET /dashboards?workspace_id=…&filter[archived]=true|false. workspace_id is
   * REQUIRED server-side (chart-service 400s without it); `archived` is a strict
   * equality filter, not "include archived too" (defaults to false = live only). */
  dashboards(workspaceId: string, limit: number, cursor?: string, archived?: boolean): Promise<Page<DashboardDTO>> {
    return this.http.get<Page<DashboardDTO>>("/api/v1/dashboards", {
      query: { workspace_id: workspaceId, limit, cursor, "filter[archived]": archived ? "true" : undefined },
    });
  }

  async chart(id: string): Promise<ChartDTO> {
    const r = await this.http.get<{ data: ChartDTO } | ChartDTO>(
      `/api/v1/charts/${encodeURIComponent(id)}`,
    );
    return unwrap<ChartDTO>(r);
  }

  /**
   * GET /dashboards/{id}/charts — enumerate a dashboard's child charts as full
   * chartView objects (id, name, chart_type, config, display_meta, sources).
   * This is the metadata list the Dashboard.charts resolver pairs with the
   * batch /data call (one list call + one batch call — no N+1).
   */
  async dashboardCharts(dashboardId: string): Promise<ChartDTO[]> {
    const r = await this.http.get<{ data: ChartDTO[] } | ChartDTO[]>(
      `/api/v1/dashboards/${encodeURIComponent(dashboardId)}/charts`,
    );
    return Array.isArray(r) ? r : (r.data ?? []);
  }

  /**
   * POST /dashboards/{id}/data — batch-resolve every chart of a dashboard in a
   * single downstream call (AC-1: <=2 calls, not one per chart). Per-chart
   * isolation is preserved: each entry carries its own result or error.
   * `filters` carries cross-filter predicates (CHART-FR-041): the chart-service
   * batch handler applies each to same-model sibling charts, skipping the origin.
   */
  dashboardData(
    dashboardId: string,
    filters?: Array<{ field: string; op: string; value: unknown; origin?: string }>,
  ): Promise<{ data: ChartDataDTO[] } | ChartDataDTO[]> {
    const body = filters && filters.length ? { filters } : undefined;
    return this.http.post(
      `/api/v1/dashboards/${encodeURIComponent(dashboardId)}/data`,
      body ? { body } : undefined,
    );
  }

  /** GET /charts/{id}/data — single chart data (used outside a dashboard batch). */
  chartData(chartId: string): Promise<ChartDataDTO> {
    return this.http.get<ChartDataDTO>(`/api/v1/charts/${encodeURIComponent(chartId)}/data`);
  }

  // ---- catalog -------------------------------------------------------------
  /** GET /chart-types — the chart-type catalog (auth only, no tenant data).
   * Powers the no-code editor's type picker + per-type config forms. */
  async chartTypes(): Promise<ChartTypeDTO[]> {
    const r = await this.http.get<{ data: ChartTypeDTO[] }>("/api/v1/chart-types");
    return r.data ?? [];
  }

  // ---- dashboard authoring -------------------------------------------------
  /** POST /dashboards — create a dashboard (201). workspace_id is sourced from
   * the caller's JWT claim by the resolver (the SDL input omits it). */
  async createDashboard(body: CreateDashboardBody, idempotencyKey?: string): Promise<DashboardDTO> {
    const r = await this.http.post<{ data: DashboardDTO } | DashboardDTO>("/api/v1/dashboards", {
      body,
      idempotencyKey,
    });
    return unwrap<DashboardDTO>(r);
  }

  /** PATCH /dashboards/{id} — partial update (200). */
  async updateDashboard(id: string, body: UpdateDashboardBody, idempotencyKey?: string): Promise<DashboardDTO> {
    const r = await this.http.patch<{ data: DashboardDTO } | DashboardDTO>(
      `/api/v1/dashboards/${encodeURIComponent(id)}`,
      { body, idempotencyKey },
    );
    return unwrap<DashboardDTO>(r);
  }

  /** DELETE /dashboards/{id} — delete (204). */
  async deleteDashboard(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/dashboards/${encodeURIComponent(id)}`);
  }

  /** POST /dashboards/{id}/archive — soft-archive (authorized as chart.dashboard.update,
   * the canonical verb for a flag flip; not a distinct archive action). Cascades
   * to the dashboard's documentation rows server-side. */
  async archiveDashboard(id: string): Promise<DashboardDTO> {
    const r = await this.http.post<{ data: DashboardDTO } | DashboardDTO>(
      `/api/v1/dashboards/${encodeURIComponent(id)}/archive`,
    );
    return unwrap<DashboardDTO>(r);
  }

  /** PATCH /dashboards/{id}/restore — clear the archived flag (also chart.dashboard.update). */
  async restoreDashboard(id: string): Promise<DashboardDTO> {
    const r = await this.http.patch<{ data: DashboardDTO } | DashboardDTO>(
      `/api/v1/dashboards/${encodeURIComponent(id)}/restore`,
    );
    return unwrap<DashboardDTO>(r);
  }

  // ---- chart authoring -----------------------------------------------------
  /** POST /dashboards/{id}/charts — create a chart on a dashboard (201). */
  async createChart(dashboardId: string, body: CreateChartBody, idempotencyKey?: string): Promise<ChartDTO> {
    const r = await this.http.post<{ data: ChartDTO } | ChartDTO>(
      `/api/v1/dashboards/${encodeURIComponent(dashboardId)}/charts`,
      { body, idempotencyKey },
    );
    return unwrap<ChartDTO>(r);
  }

  /** PATCH /charts/{id} — partial update (200). */
  async updateChart(id: string, body: UpdateChartBody, idempotencyKey?: string): Promise<ChartDTO> {
    const r = await this.http.patch<{ data: ChartDTO } | ChartDTO>(
      `/api/v1/charts/${encodeURIComponent(id)}`,
      { body, idempotencyKey },
    );
    return unwrap<ChartDTO>(r);
  }

  /** DELETE /charts/{id} — delete (204). */
  async deleteChart(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/charts/${encodeURIComponent(id)}`);
  }

  /** POST /charts/preview — resolve an UNSAVED chart spec inline for the live
   * editor preview (never cached; row-capped server-side). */
  async preview(body: PreviewChartBody): Promise<ChartShapedDataDTO> {
    const r = await this.http.post<{ data: ChartShapedDataDTO } | ChartShapedDataDTO>(
      "/api/v1/charts/preview",
      { body },
    );
    return unwrap<ChartShapedDataDTO>(r);
  }
}

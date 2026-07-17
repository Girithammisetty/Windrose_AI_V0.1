/**
 * Pure chart-spec serialization for the no-code chart editor. Turns the picked
 * chart type + semantic-model encodings (x = dimension, y = measures with their
 * agg) into the exact `config` / `displayMeta` / `sources` payload the
 * chart-service accepts (matches services/chart-service config shapes). Kept
 * framework-free so the serialization is unit-testable in isolation.
 *
 * Example — a bar chart over `claims_core` (claim_type × claim_count):
 *   config      = { x: { dimension: "claim_type" },
 *                   y: [{ measure: "claim_count", agg_fn: "count" }] }
 *   displayMeta = { semantic_model: "claims_core", workspace_id: "<ws>" }
 *   sources     = [{ position: 0, sourceType: "semantic_measure",
 *                    sourceUrn: "wr:<tenant>:semantic:measure/claim_count" }]
 */
import type {
  ChartSourceInput,
  JSONValue,
  SemanticMeasure,
} from "@/lib/graphql/types";

/** The friendly chart types offered in the editor's picker, mapped 1:1 to the
 * chart-service catalog (27 of the 30 types — every `dataClass: "query"` +
 * `"dataset"` type; the 3 `dataClass: "run"` types (roc_curve, confusion_matrix,
 * decision_tree) are out of scope, see spec.ts module docs). `group` drives the
 * picker's <optgroup> so the 27 options stay scannable. Required encodings are
 * driven from `family` (axis/y_only/grid share one shape; heatmap/network/metric
 * each have their own — see requiredEncodings + the Heatmap/Network/Metric
 * helpers below). */
export const FRIENDLY_CHART_TYPES = [
  // axis (x + y, optional dataseries for stack-by / series-split)
  { key: "bar", chartType: "vertical_bar_chart", label: "Bar", group: "Axis (x / y)" },
  { key: "stacked_bar", chartType: "vertical_stackedbar_chart", label: "Stacked bar", group: "Axis (x / y)" },
  { key: "line", chartType: "line_chart", label: "Line", group: "Axis (x / y)" },
  { key: "scatter", chartType: "scatter_plot", label: "Scatter plot", group: "Axis (x / y)" },
  { key: "bubble", chartType: "bubble_chart", label: "Bubble", group: "Axis (x / y)" },
  { key: "whisker", chartType: "whisker_chart", label: "Whisker (box plot)", group: "Axis (x / y)" },
  { key: "combination", chartType: "combination_chart", label: "Combination", group: "Axis (x / y)" },
  { key: "geo_map", chartType: "geo_map_chart", label: "Geo map", group: "Axis (x / y)" },
  { key: "histogram", chartType: "histogram_chart", label: "Histogram", group: "Axis (x / y)" },
  { key: "waterfall", chartType: "waterfall_chart", label: "Waterfall", group: "Axis (x / y)" },
  // y_only (measures only, no x required)
  { key: "pie", chartType: "pie_chart", label: "Pie", group: "Single measure" },
  { key: "funnel", chartType: "funnel_chart", label: "Funnel", group: "Single measure" },
  { key: "gauge", chartType: "gauge_chart", label: "Gauge", group: "Single measure" },
  { key: "word_cloud", chartType: "word_cloud_chart", label: "Word cloud", group: "Single measure" },
  // grid (columns pass-through)
  { key: "grid", chartType: "grid_chart", label: "Table", group: "Table" },
  { key: "pivot_table", chartType: "pivot_table_chart", label: "Pivot table", group: "Table" },
  // heatmap (x + y + dataseries, all dimensions)
  { key: "heatmap", chartType: "heatmap_chart", label: "Heat map", group: "Matrix / hierarchy" },
  { key: "sunburst", chartType: "sunburst_chart", label: "Sunburst", group: "Matrix / hierarchy" },
  { key: "tree_map", chartType: "tree_map_chart", label: "Tree map", group: "Matrix / hierarchy" },
  { key: "sankey", chartType: "sankey_chart", label: "Sankey", group: "Matrix / hierarchy" },
  { key: "chord", chartType: "chord_chart", label: "Chord", group: "Matrix / hierarchy" },
  // network (nodes + children column names, backed by a saved query)
  { key: "network", chartType: "network_chart", label: "Network", group: "Network / tree" },
  { key: "network_graph", chartType: "network_graph_chart", label: "Network graph", group: "Network / tree" },
  { key: "tree", chartType: "tree_chart", label: "Tree", group: "Network / tree" },
  { key: "decision_tree", chartType: "decision_tree_chart", label: "Decision tree", group: "Network / tree" },
  // metric (dataClass "dataset" — a dataset source only, no x/y)
  { key: "metric", chartType: "metric_chart", label: "Metric", group: "Single value" },
  { key: "parameter", chartType: "parameter_chart", label: "Parameter", group: "Single value" },
] as const;

export type FriendlyChartKey = (typeof FRIENDLY_CHART_TYPES)[number]["key"];

/** The catalog names we expose (used to filter the full chartTypes catalog). */
export const FRIENDLY_CHART_NAMES: string[] = FRIENDLY_CHART_TYPES.map((f) => f.chartType);

/** One selected y-measure encoding (measure name + aggregation). */
export interface MeasureEncoding {
  measure: string;
  agg: string;
}

/** The editor's current encoding selection (axis / y_only / grid families —
 * all three share this x-dimension + y-measures[] shape per chart-service's
 * generic per-family config schema, confirmed against
 * services/chart-service/internal/domain/charttypes.go: there is NO per-type
 * config beyond the family schema, e.g. bubble/combination/stacked-bar do not
 * get their own fields — they all reuse the one optional `dataseries`
 * dimension below). */
export interface Encodings {
  /** x dimension name (category axis / pie label). */
  x?: string;
  /** y measures (>= 1 for every family we offer). */
  y: MeasureEncoding[];
  /** Optional series-split dimension (axis family only, CHART-FR-012's
   * `dataseries` field) — drives stack-by (stacked bar), per-series split
   * (combination/whisker/geo_map/waterfall/...), never required. */
  dataseries?: string;
}

/** Whether x / y encodings are required for a given config family. Only
 * covers the axis/y_only/grid families — heatmap/network/metric have their
 * own dedicated shapes + validators below (their config isn't x-dimension +
 * y-measures[]). */
export function requiredEncodings(family: string): { x: boolean; y: boolean } {
  switch (family) {
    case "axis":
      return { x: true, y: true };
    case "grid":
      return { x: true, y: true };
    case "y_only":
      return { x: false, y: true };
    default:
      return { x: false, y: true };
  }
}

/** The shaped column name a measure produces (matches chart-service Shape). */
export function measureColName(measure: string, agg?: string | null): string {
  return agg ? `${agg}_${measure}` : measure;
}

export interface EncodingError {
  field: "x" | "y" | "dataseries" | "nodes" | "children" | "source";
  message: string;
}

/** Validate the selected encodings against the family's required fields. */
export function validateEncodings(family: string, enc: Encodings): EncodingError[] {
  const req = requiredEncodings(family);
  const errors: EncodingError[] = [];
  if (req.x && !enc.x) errors.push({ field: "x", message: "Pick a dimension for the x-axis." });
  if (req.y && enc.y.length === 0) errors.push({ field: "y", message: "Pick at least one measure." });
  return errors;
}

/** Build the chart-service `config` object for a family + encodings. */
export function buildChartConfig(family: string, enc: Encodings): JSONValue {
  const y = enc.y.map((m) => ({ measure: m.measure, agg_fn: m.agg }));
  switch (family) {
    case "axis": {
      const cfg: Record<string, JSONValue> = { x: enc.x ? { dimension: enc.x } : null, y: y as JSONValue };
      if (enc.dataseries) cfg.dataseries = { dimension: enc.dataseries };
      return cfg;
    }
    case "y_only":
      return enc.x ? { x: { dimension: enc.x }, y } : { y };
    case "grid":
      return {
        x: enc.x ? { dimension: enc.x } : null,
        y,
        columns: [
          ...(enc.x ? [enc.x] : []),
          ...enc.y.map((m) => measureColName(m.measure, m.agg)),
        ],
      };
    case "metric":
      return {};
    default:
      return { y };
  }
}

/* ---------------------------------------------------------------------------
 * Heatmap family (sunburst_chart, sankey_chart, tree_map_chart, heatmap_chart,
 * chord_chart) — CHART-FR-012's heatmap schema is {x, y, dataseries}, ALL
 * dimension refs (unlike axis, heatmap's `y` is a dimension, not a measure —
 * confirmed against charttypes.go's schemaForFamily/ValidateConfig, family
 * "heatmap"). All three are required.
 * ------------------------------------------------------------------------ */
export interface HeatmapEncodings {
  x?: string;
  y?: string;
  dataseries?: string;
}

export function validateHeatmapEncodings(enc: HeatmapEncodings): EncodingError[] {
  const errors: EncodingError[] = [];
  if (!enc.x) errors.push({ field: "x", message: "Pick a dimension for the x-axis." });
  if (!enc.y) errors.push({ field: "y", message: "Pick a dimension for the y-axis." });
  if (!enc.dataseries) errors.push({ field: "dataseries", message: "Pick a series dimension." });
  return errors;
}

/** Build the heatmap-family config (x/y/dataseries all {dimension} refs). */
export function buildHeatmapConfig(enc: HeatmapEncodings): JSONValue {
  return {
    x: enc.x ? { dimension: enc.x } : null,
    y: enc.y ? { dimension: enc.y } : null,
    dataseries: enc.dataseries ? { dimension: enc.dataseries } : null,
  };
}

/* ---------------------------------------------------------------------------
 * Network family (decision_tree_chart, network_graph_chart, network_chart,
 * tree_chart) — config is {nodes, children, node_values?}, plain COLUMN NAMES
 * (not dimension refs). Per chart-service's resolver (buildCompile requires
 * >=1 y-measure to run a semantic-measure source, which network configs never
 * have) network charts can only resolve over a SAVED QUERY source
 * (source_type "saved_query") whose SELECT list's first 2-3 columns are
 * (parent, child, value?) — shapeNetwork reads rows by POSITION, so `nodes` /
 * `children` here are the human-readable labels for those columns, not a
 * lookup key chart-service re-resolves.
 * ------------------------------------------------------------------------ */
export interface NetworkEncodings {
  nodes?: string;
  children?: string;
  nodeValues?: string;
}

export function validateNetworkEncodings(enc: NetworkEncodings): EncodingError[] {
  const errors: EncodingError[] = [];
  if (!enc.nodes) errors.push({ field: "nodes", message: "Name the parent/node column." });
  if (!enc.children) errors.push({ field: "children", message: "Name the child column." });
  return errors;
}

export function buildNetworkConfig(enc: NetworkEncodings): JSONValue {
  const cfg: Record<string, string> = { nodes: enc.nodes ?? "", children: enc.children ?? "" };
  if (enc.nodeValues) cfg.node_values = enc.nodeValues;
  return cfg;
}

/** A typed `sources` list pointing at a saved query (network family's only
 * resolvable source — see NetworkEncodings docs above). */
export function buildSavedQuerySource(queryUrn: string): ChartSourceInput[] {
  return [{ position: 0, sourceType: "saved_query", sourceUrn: queryUrn }];
}

/* ---------------------------------------------------------------------------
 * Metric family, dataClass "dataset" (metric_chart, parameter_chart) — no x/y
 * encodings at all; chart-service's resolveArtifact() fetches the artifact
 * for `sources[0]` directly, so authoring is just "pick a dataset". Config is
 * always {} (FamilyMetric's schema has no properties).
 * ------------------------------------------------------------------------ */
export function validateMetricSource(datasetUrn?: string): EncodingError[] {
  return datasetUrn ? [] : [{ field: "source", message: "Pick a dataset." }];
}

/** A typed `sources` list pointing at a dataset (metric family's source). */
export function buildDatasetSource(datasetUrn: string): ChartSourceInput[] {
  return [{ position: 0, sourceType: "dataset", sourceUrn: datasetUrn }];
}

/** Build `displayMeta` — carries the semantic model name (chart-service reads
 * `display_meta.semantic_model`) and the workspace for resolution. */
export function buildDisplayMeta(modelName: string, workspaceId: string): JSONValue {
  return { semantic_model: modelName, workspace_id: workspaceId };
}

/**
 * Build the typed `sources` list. The source_type ("semantic_measure") is the
 * load-bearing signal; the URN is derived from the model's first measure
 * (best-effort marker when the tenant isn't known client-side).
 */
export function buildSources(firstMeasure: string, tenantId?: string): ChartSourceInput[] {
  const tenant = tenantId && tenantId.trim() ? tenantId : "tenant";
  return [
    {
      position: 0,
      sourceType: "semantic_measure",
      sourceUrn: `wr:${tenant}:semantic:measure/${firstMeasure}`,
    },
  ];
}

export interface ChartSpec {
  config: JSONValue;
  displayMeta: JSONValue;
  sources: ChartSourceInput[];
}

/** Serialize the full create-chart spec (config + displayMeta + sources). */
export function buildChartSpec(args: {
  family: string;
  encodings: Encodings;
  modelName: string;
  workspaceId: string;
  tenantId?: string;
  /** The model's first measure name (for the source URN). Falls back to the
   * first selected y-measure when the full model isn't loaded. */
  firstMeasure?: string;
}): ChartSpec {
  const first = args.firstMeasure ?? args.encodings.y[0]?.measure ?? "measure";
  return {
    config: buildChartConfig(args.family, args.encodings),
    displayMeta: buildDisplayMeta(args.modelName, args.workspaceId),
    sources: buildSources(first, args.tenantId),
  };
}

/** Default agg for a measure: its declared agg, else "count" (always allowed). */
export function defaultAgg(measure: Pick<SemanticMeasure, "agg">): string {
  return measure.agg ?? "count";
}

/** The chart-service agg whitelist (CHART-FR-014) — drives the agg picker. */
export const ALLOWED_AGG_FNS = ["sum", "avg", "min", "max", "count", "first"] as const;

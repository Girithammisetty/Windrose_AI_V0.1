"use client";
import { BarChart } from "./BarChart";
import { LineChart } from "./LineChart";
import { PieChart } from "./PieChart";
import { GridChart } from "./GridChart";
import { HeatmapChart } from "./HeatmapChart";
import { GaugeChart } from "./GaugeChart";
import { WordCloudChart } from "./WordCloudChart";
import { NetworkChart } from "./NetworkChart";
import { MetricChart } from "./MetricChart";

/**
 * The single dispatch point for rendering shaped chart data as a real chart.
 * Given { columns, rows } (the ShapedResult shape) plus the chart type/family,
 * it picks the right renderer. Bespoke inline-SVG renderers exist for: bar
 * (+ every other axis-family type — scatter/bubble/whisker/combination/geo_map/
 * histogram/waterfall/stacked-bar all reuse it, see BarChart's dataseries-pivot
 * support in geometry.ts), line, pie, gauge, word cloud, heatmap, and the grid
 * pass-through (used by grid_chart/pivot_table_chart AND as the tabular
 * fallback for the other heatmap-family types — sunburst/sankey/tree_map/
 * chord — CHART-FR per the report, a bespoke visual for those was out of scope
 * for time). network + metric families render an honest real-data-or-explicit-
 * gap state (see NetworkChart/MetricChart docs — chart-service ships their
 * data as `graph`/`artifact`, which bff-graphql does not yet expose).
 *
 * chartType wins when it maps to a known renderer; otherwise we fall back to
 * the family, then to a bar chart. Empty/loading/error are handled by the
 * caller's AsyncBoundary — this component only renders data it is given.
 */
export function ChartView({
  chartType,
  family,
  columns,
  rows,
  artifact,
  title,
  desc,
  onSelect,
  selectedValue,
}: {
  chartType?: string | null;
  family?: string | null;
  columns: unknown;
  rows: unknown;
  /** Resolved artifact blob for the metric/parameter family (chart-service
   * ShapedResult.artifact) — passed through to MetricChart. */
  artifact?: unknown;
  title?: string;
  desc?: string;
  /** Cross-filter: called with the clicked category when this chart is a
   * selectable source (bar / pie / grid). Omit to render non-interactive. */
  onSelect?: (value: string) => void;
  /** The currently-selected category for this chart (highlights the mark). */
  selectedValue?: string | null;
}) {
  const kind = resolveKind(chartType, family);
  switch (kind) {
    case "grid":
      return <GridChart columns={columns} rows={rows} title={title} onSelect={onSelect} selectedValue={selectedValue} />;
    case "pie":
      return <PieChart columns={columns} rows={rows} title={title} desc={desc} onSelect={onSelect} selectedValue={selectedValue} />;
    case "line":
      return <LineChart columns={columns} rows={rows} title={title} desc={desc} />;
    case "heatmap":
      return <HeatmapChart columns={columns} rows={rows} title={title} desc={desc} />;
    case "gauge":
      return <GaugeChart columns={columns} rows={rows} title={title} desc={desc} />;
    case "wordcloud":
      return <WordCloudChart columns={columns} rows={rows} title={title} />;
    case "network":
      return <NetworkChart columns={columns} rows={rows} title={title} />;
    case "metric":
      return <MetricChart columns={columns} rows={rows} artifact={artifact} title={title} />;
    default:
      return <BarChart columns={columns} rows={rows} title={title} desc={desc} />;
  }
}

type RenderKind = "bar" | "line" | "pie" | "grid" | "heatmap" | "gauge" | "wordcloud" | "network" | "metric";

/** Exact chart-type-name → renderer kind, covering every type FRIENDLY_CHART_TYPES
 * offers (services/ui-web/src/lib/charts/spec.ts). Checked before the substring
 * fallback below so e.g. "scatter_plot" (no "bar"/"line"/"pie" substring) still
 * resolves deterministically instead of falling through to family/default. */
const EXACT_KIND: Record<string, RenderKind> = {
  vertical_bar_chart: "bar",
  vertical_stackedbar_chart: "bar",
  scatter_plot: "bar",
  bubble_chart: "bar",
  whisker_chart: "bar",
  combination_chart: "bar",
  geo_map_chart: "bar",
  histogram_chart: "bar",
  waterfall_chart: "bar",
  line_chart: "line",
  pie_chart: "pie",
  funnel_chart: "bar",
  gauge_chart: "gauge",
  word_cloud_chart: "wordcloud",
  grid_chart: "grid",
  pivot_table_chart: "grid",
  heatmap_chart: "heatmap",
  sunburst_chart: "grid",
  sankey_chart: "grid",
  tree_map_chart: "grid",
  chord_chart: "grid",
  network_chart: "network",
  network_graph_chart: "network",
  tree_chart: "network",
  decision_tree_chart: "network",
  metric_chart: "metric",
  parameter_chart: "metric",
};

/** Resolve chartType (preferred) then family to a renderer kind. */
export function resolveKind(chartType?: string | null, family?: string | null): RenderKind {
  const t = (chartType ?? "").toLowerCase();
  if (t && EXACT_KIND[t]) return EXACT_KIND[t];
  if (t.includes("grid") || t.includes("pivot")) return "grid";
  if (t.includes("pie") || t.includes("donut")) return "pie";
  if (t.includes("line") || t.includes("area")) return "line";
  if (t.includes("bar")) return "bar";

  switch ((family ?? "").toLowerCase()) {
    case "grid":
      return "grid";
    case "y_only":
      return "pie";
    case "heatmap":
      return "grid";
    case "network":
      return "network";
    case "metric":
      return "metric";
    case "axis":
      return "bar";
    default:
      return "bar";
  }
}

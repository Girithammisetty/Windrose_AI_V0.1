"use client";
import { toLabel } from "@/lib/charts/geometry";
import { t } from "@/lib/i18n/messages";

/**
 * Renderer for the network family (network_chart, network_graph_chart,
 * tree_chart, decision_tree_chart). chart-service shapes this family as a
 * `{nodes, edges}` GRAPH object (services/chart-service/internal/domain/
 * shape.go shapeNetwork), not tabular columns/rows — and that `graph` field is
 * not yet selected by bff-graphql's ChartShapedData/ChartData GraphQL types
 * (services/bff-graphql/src/schema/typeDefs.ts), so `rows`/`columns` are
 * always empty for this family via the current chartPreview/chart.data APIs.
 * This is a real, confirmed platform gap (not a mock) — see the chart-builder
 * report for the exact fix needed. If `rows` ever does carry tabular
 * [parent, child, value?] data (e.g. a future BFF change threading `graph`
 * through as rows), render it as a real edge list rather than silently
 * dropping it.
 */
export function NetworkChart({
  rows,
  title,
}: {
  columns?: unknown;
  rows: unknown;
  title?: string;
}) {
  const rws = Array.isArray(rows) ? (rows as unknown[][]) : [];
  if (rws.length === 0) {
    return (
      <div className="py-6 text-center text-xs text-muted-foreground" role="status">
        <p>{t("charts.networkPreviewUnsupported")}</p>
      </div>
    );
  }
  return (
    <ul aria-label={title ?? "Network edges"} className="max-h-64 space-y-1 overflow-y-auto text-xs">
      {rws.map((r, i) => (
        <li key={i} className="flex items-center gap-2 rounded border px-2 py-1">
          <span className="font-mono">{toLabel(r[0])}</span>
          <span aria-hidden className="text-muted-foreground">
            →
          </span>
          <span className="font-mono">{toLabel(r[1])}</span>
          {r[2] != null && <span className="ml-auto text-muted-foreground">{toLabel(r[2])}</span>}
        </li>
      ))}
    </ul>
  );
}

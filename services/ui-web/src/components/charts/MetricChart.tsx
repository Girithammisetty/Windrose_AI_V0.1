"use client";
import { toLabel } from "@/lib/charts/geometry";
import { t } from "@/lib/i18n/messages";

/**
 * Renderer for the metric family (metric_chart, parameter_chart, and the
 * dataClass="run" roc_curve/confusion_matrix/decision_tree if ever previewed).
 * chart-service resolves this family via resolveArtifact() into an `artifact`
 * JSON blob (services/chart-service/internal/resolve/resolver.go), not tabular
 * columns/rows. bff-graphql now selects `artifact` on ChartData/ChartShapedData,
 * so a dataset metric chart arrives here as {kind:"dataset_summary",
 * metrics:[{label,value}]} (dataset-service GET /api/v1/artifacts renders the
 * dataset's profile summary as headline metrics). Primary render is that
 * artifact's key/value metrics; any real tabular columns/rows are rendered as a
 * defensive fallback (and the explicit gap state when neither is present).
 */
type MetricEntry = { label: string; value: unknown };

/** Narrow an unknown artifact blob to its {metrics:[{label,value}]} entries. */
function metricEntries(artifact: unknown): MetricEntry[] | null {
  if (!artifact || typeof artifact !== "object") return null;
  const raw = (artifact as { metrics?: unknown }).metrics;
  if (!Array.isArray(raw)) return null;
  const entries = raw.filter(
    (m): m is MetricEntry =>
      !!m && typeof m === "object" && typeof (m as { label?: unknown }).label === "string",
  );
  return entries.length > 0 ? entries : null;
}

export function MetricChart({
  columns,
  rows,
  artifact,
  title,
}: {
  columns: unknown;
  rows: unknown;
  artifact?: unknown;
  title?: string;
}) {
  const entries = metricEntries(artifact);
  if (entries) {
    return (
      <dl aria-label={title ?? "Metric"} className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        {entries.map((m, i) => (
          <div key={i} className="contents">
            <dt className="text-muted-foreground">{toLabel(m.label)}</dt>
            <dd className="font-mono">{toLabel(m.value)}</dd>
          </div>
        ))}
      </dl>
    );
  }

  const cols = Array.isArray(columns) ? columns.map(toLabel) : [];
  const rws = Array.isArray(rows) ? (rows as unknown[][]) : [];
  if (rws.length === 0) {
    return (
      <div className="py-6 text-center text-xs text-muted-foreground" role="status">
        <p>{t("charts.metricPreviewUnsupported")}</p>
      </div>
    );
  }
  const row = rws[0];
  return (
    <dl aria-label={title ?? "Metric"} className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
      {cols.map((c, i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{c}</dt>
          <dd className="font-mono">{toLabel(row[i])}</dd>
        </div>
      ))}
    </dl>
  );
}

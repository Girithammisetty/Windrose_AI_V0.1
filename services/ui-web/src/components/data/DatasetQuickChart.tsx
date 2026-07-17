"use client";
import { useMemo, useState } from "react";
import { useDatasetRows, useDatasetAggregate } from "@/lib/graphql/hooks";
import { ChartView } from "@/components/charts/ChartView";
import { Label } from "@/components/ui/primitives";

const AGGS = ["count", "sum", "avg", "min", "max"] as const;
const CHART_TYPES = [
  { value: "vertical_bar_chart", label: "Bar" },
  { value: "pie_chart", label: "Pie" },
  { value: "line_chart", label: "Line" },
] as const;

type Agg = (typeof AGGS)[number];

/**
 * Quick-chart a raw dataset WITHOUT authoring a semantic model: pick a
 * dimension (group-by), a measure + aggregation, and a chart type. The
 * aggregation runs in the warehouse (BFF `datasetAggregate` → query-service
 * GROUP BY over the dataset's {{dataset()}} macro) — never in the browser —
 * and the result renders through the same ChartView the dashboards use.
 */
export function DatasetQuickChart({ datasetId }: { datasetId: string }) {
  // Column list comes from a 1-row peek (authoritative physical columns).
  const head = useDatasetRows(datasetId, { offset: 0, limit: 1 });
  const columns = useMemo(() => head.data?.datasetRows.columns ?? [], [head.data]);

  const [dimension, setDimension] = useState("");
  const [measure, setMeasure] = useState(""); // "" ⇒ count(*)
  const [agg, setAgg] = useState<Agg>("count");
  const [chartType, setChartType] = useState<string>("vertical_bar_chart");

  // Default the dimension to the first column once columns load.
  const dim = dimension || columns[0] || "";
  const needsMeasure = agg !== "count";

  const { data, isFetching, isError, error } = useDatasetAggregate(
    datasetId,
    { dimension: dim, measure: needsMeasure ? measure : measure || null, agg, limit: 50 },
    { enabled: !!dim && (!needsMeasure || !!measure) },
  );

  const aggResult = data?.datasetAggregate;

  return (
    <div className="space-y-4">
      {/* controls */}
      <div className="flex flex-wrap items-end gap-3 rounded-md border bg-muted/30 p-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="qc-dim" className="text-xs">
            Group by
          </Label>
          <select
            id="qc-dim"
            value={dim}
            onChange={(e) => setDimension(e.target.value)}
            className="rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <Label htmlFor="qc-agg" className="text-xs">
            Aggregate
          </Label>
          <select
            id="qc-agg"
            value={agg}
            onChange={(e) => setAgg(e.target.value as Agg)}
            className="rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            {AGGS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <Label htmlFor="qc-measure" className="text-xs">
            Of column {needsMeasure && <span className="text-destructive">*</span>}
          </Label>
          <select
            id="qc-measure"
            value={measure}
            onChange={(e) => setMeasure(e.target.value)}
            disabled={!needsMeasure}
            className="rounded-md border bg-background px-2 py-1.5 text-sm disabled:opacity-50"
          >
            <option value="">{needsMeasure ? "— choose a column —" : "(rows)"}</option>
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <Label htmlFor="qc-type" className="text-xs">
            Chart
          </Label>
          <select
            id="qc-type"
            value={chartType}
            onChange={(e) => setChartType(e.target.value)}
            className="rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            {CHART_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {isFetching && <span className="pb-1.5 text-xs text-muted-foreground">running…</span>}
      </div>

      {/* chart */}
      {needsMeasure && !measure ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          Choose a column to {agg}.
        </p>
      ) : isError ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          Could not aggregate: {(error as Error)?.message ?? "unknown error"}
        </p>
      ) : aggResult ? (
        <div className="space-y-3">
          <div className="rounded-lg border p-4">
            <ChartView
              chartType={chartType}
              columns={aggResult.columns}
              rows={aggResult.rows}
              title={`${agg}${measure ? ` of ${measure}` : ""} by ${dim}`}
            />
          </div>
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">Show generated query</summary>
            <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 font-mono">{aggResult.sql}</pre>
          </details>
        </div>
      ) : (
        <p className="py-10 text-center text-sm text-muted-foreground">Loading…</p>
      )}
    </div>
  );
}

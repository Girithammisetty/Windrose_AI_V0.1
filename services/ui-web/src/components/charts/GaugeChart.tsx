"use client";
import { useId } from "react";
import { toChartModel, arcPath, toNumber } from "@/lib/charts/geometry";

/**
 * Inline-SVG half-donut gauge (no charting dependency) for `gauge_chart`
 * (y_only family, single measure). chart-service's config has no
 * target/threshold field for gauges (confirmed against charttypes.go — the
 * y_only schema is just `{y}`), so there is no real "goal" to compare
 * against; the arc fill is headroom-relative (the value against 1.25× itself,
 * or against the largest sibling category when the model picked an x
 * dimension too) purely so the sweep reads proportionally, not as a fake KPI
 * target. The exact number is always printed below the arc.
 */
export function GaugeChart({
  columns,
  rows,
  title,
  desc,
}: {
  columns: unknown;
  rows: unknown;
  title?: string;
  desc?: string;
}) {
  const model = toChartModel(columns, rows);
  const titleId = useId();
  const descId = useId();

  const values = model.series[0]?.values ?? [];
  const label = model.categories[0] ?? model.series[0]?.name ?? "";
  const value = values[0] ?? 0;
  const headroom = Math.max(...values, value * 1.25, 1);

  if (values.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">No values to plot.</p>;
  }

  const size = 220;
  const cx = size / 2;
  const cy = size / 2 + 10;
  const r = 88;
  const sweep = Math.max(0, Math.min(1, value / headroom)) * 180;

  return (
    <div className="flex w-full flex-col items-center">
      <svg
        viewBox={`0 0 ${size} ${size / 2 + 30}`}
        className="h-auto w-full max-w-[220px] text-muted-foreground"
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
      >
        <title id={titleId}>{title ?? "Gauge"}</title>
        <desc id={descId}>{desc ?? `Gauge of ${model.series[0]?.name ?? "value"}: ${value}`}</desc>
        <path d={arcPath(cx, cy, r, -90, 90)} fill="none" stroke="currentColor" strokeOpacity={0.12} strokeWidth={16} />
        {sweep > 0 && (
          <path d={arcPath(cx, cy, r, -90, -90 + sweep)} fill="none" stroke="hsl(211 90% 48%)" strokeWidth={16}>
            <title>{`${label}: ${value}`}</title>
          </path>
        )}
        <text x={cx} y={cy - 6} textAnchor="middle" fontSize={26} fontWeight={600} fill="currentColor">
          {formatValue(value)}
        </text>
        {label && (
          <text x={cx} y={cy + 16} textAnchor="middle" fontSize={11} fill="currentColor" fillOpacity={0.7}>
            {label}
          </text>
        )}
      </svg>
    </div>
  );
}

function formatValue(v: number): string {
  const n = toNumber(v);
  if (Math.abs(n) >= 1000) return `${Math.round(n / 100) / 10}k`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

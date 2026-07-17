"use client";
import { useId } from "react";
import { toChartModel, pieSlices, arcPath, paletteColor, toLabel } from "@/lib/charts/geometry";
import { ChartLegend } from "./ChartLegend";

/**
 * Inline-SVG pie chart (no charting dependency) for the y_only family. Uses the
 * FIRST value series; each slice's sweep is proportional to its value and the
 * sweeps sum to 360°. Slices carry a <title> for hover (label + value + %).
 */
export function PieChart({
  columns,
  rows,
  title,
  desc,
  onSelect,
  selectedValue,
}: {
  columns: unknown;
  rows: unknown;
  title?: string;
  desc?: string;
  /** Cross-filter: clicking a slice emits its label (CHART-FR-041). */
  onSelect?: (value: string) => void;
  /** The currently-selected label; its slice stays lit while others dim. */
  selectedValue?: string | null;
}) {
  const model = toChartModel(columns, rows);
  const titleId = useId();
  const descId = useId();

  const series = model.series[0];
  const labels = model.categories.length ? model.categories : (series?.values ?? []).map((_, i) => `#${i + 1}`);
  const values = series?.values ?? [];
  const slices = pieSlices(values);
  const total = values.reduce((s, v) => s + Math.max(0, v), 0);

  const size = 240;
  const r = 96;
  const cx = size / 2;
  const cy = size / 2;

  if (slices.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">No values to plot.</p>;
  }

  return (
    <div className="flex w-full flex-col items-center">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="h-auto w-full max-w-[240px] text-muted-foreground"
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={titleId}>{title ?? "Pie chart"}</title>
        <desc id={descId}>{desc ?? `Pie chart of ${series?.name ?? "values"}`}</desc>
        {slices.map((slice, i) => {
          const label = toLabel(labels[i]);
          const selectable = !!onSelect;
          const hasSelection = selectedValue != null;
          const isSelected = hasSelection && label === String(selectedValue);
          return (
            <path
              key={i}
              d={arcPath(cx, cy, r, slice.startAngle, slice.endAngle)}
              fill={paletteColor(i)}
              stroke={isSelected ? "currentColor" : "var(--card, #fff)"}
              strokeWidth={isSelected ? 2 : 1}
              opacity={!hasSelection || isSelected ? 1 : 0.32}
              onClick={selectable ? () => onSelect(label) : undefined}
              style={selectable ? { cursor: "pointer" } : undefined}
            >
              <title>{`${label}: ${slice.value} (${Math.round(slice.fraction * 100)}%)`}</title>
            </path>
          );
        })}
      </svg>
      <ChartLegend series={labels.map((l, i) => `${toLabel(l)} (${pct(values[i], total)})`)} />
    </div>
  );
}

function pct(v: number, total: number): string {
  if (total <= 0) return "0%";
  return `${Math.round((Math.max(0, v) / total) * 100)}%`;
}

"use client";
import { useId } from "react";
import { toChartModel, maxValue, paletteColor } from "@/lib/charts/geometry";
import { ChartLegend } from "./ChartLegend";

/**
 * Inline-SVG grouped bar chart (no charting dependency). Renders one bar group
 * per category, one bar per series, scaled proportionally to the largest value.
 * Responsive via viewBox; axis text uses currentColor so it is theme-aware; each
 * bar carries a <title> so the value shows on hover (a11y + tooltip).
 */
export function BarChart({
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
  /** Cross-filter: clicking a category group emits its label (CHART-FR-041). */
  onSelect?: (value: string) => void;
  /** The currently-selected category; its group stays lit while others dim. */
  selectedValue?: string | null;
}) {
  const model = toChartModel(columns, rows);
  const titleId = useId();
  const descId = useId();

  const W = 480;
  const H = 260;
  const padL = 40;
  const padR = 12;
  const padT = 12;
  const padB = 44;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const max = maxValue(model);
  const groups = model.categories.length;
  const seriesN = Math.max(1, model.series.length);
  const groupW = groups > 0 ? plotW / groups : plotW;
  const barGap = 2;
  const barW = groups > 0 ? Math.max(2, (groupW - barGap * (seriesN + 1)) / seriesN) : 0;

  // A few horizontal gridlines with value ticks.
  const ticks = 4;
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => (max / ticks) * i);

  if (groups === 0 || max <= 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">No values to plot.</p>;
  }

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full text-muted-foreground"
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={titleId}>{title ?? "Bar chart"}</title>
        <desc id={descId}>{desc ?? `Bar chart of ${model.series.map((s) => s.name).join(", ")}`}</desc>

        {/* Gridlines + y ticks */}
        {tickVals.map((v, i) => {
          const y = padT + plotH - (v / max) * plotH;
          return (
            <g key={i}>
              <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="currentColor" strokeOpacity={0.12} />
              <text x={padL - 6} y={y + 3} textAnchor="end" fontSize={9} fill="currentColor" fillOpacity={0.7}>
                {formatTick(v)}
              </text>
            </g>
          );
        })}

        {/* x axis baseline */}
        <line x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH} stroke="currentColor" strokeOpacity={0.3} />

        {/* Bars */}
        {model.categories.map((cat, gi) => {
          const gx = padL + gi * groupW;
          const selectable = !!onSelect;
          const hasSelection = selectedValue != null;
          const isSelected = hasSelection && String(cat) === String(selectedValue);
          // When a selection is active elsewhere, dim the non-selected groups.
          const groupOpacity = !hasSelection || isSelected ? 1 : 0.32;
          return (
            <g
              key={gi}
              onClick={selectable ? () => onSelect(String(cat)) : undefined}
              style={selectable ? { cursor: "pointer" } : undefined}
              opacity={groupOpacity}
            >
              {/* Full-height hit target so the whole column is clickable. */}
              {selectable && (
                <rect x={gx} y={padT} width={groupW} height={plotH} fill="transparent" />
              )}
              {model.series.map((s, si) => {
                const v = s.values[gi] ?? 0;
                const h = (Math.max(0, v) / max) * plotH;
                const x = gx + barGap + si * (barW + barGap);
                const y = padT + plotH - h;
                return (
                  <rect
                    key={si}
                    x={x}
                    y={y}
                    width={barW}
                    height={h}
                    rx={1.5}
                    fill={paletteColor(si)}
                    stroke={isSelected ? "currentColor" : undefined}
                    strokeWidth={isSelected ? 1.5 : undefined}
                  >
                    <title>{`${cat} · ${s.name}: ${v}`}</title>
                  </rect>
                );
              })}
              <text
                x={gx + groupW / 2}
                y={padT + plotH + 14}
                textAnchor="middle"
                fontSize={9}
                fill="currentColor"
                fillOpacity={0.8}
                fontWeight={isSelected ? 700 : undefined}
              >
                {truncate(cat)}
              </text>
            </g>
          );
        })}
      </svg>
      {model.series.length > 1 && <ChartLegend series={model.series.map((s) => s.name)} />}
    </div>
  );
}

function formatTick(v: number): string {
  if (v >= 1000) return `${Math.round(v / 100) / 10}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}
function truncate(s: string, n = 10): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

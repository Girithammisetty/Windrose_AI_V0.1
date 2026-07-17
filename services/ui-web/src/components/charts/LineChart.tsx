"use client";
import { useId } from "react";
import { toChartModel, maxValue, linePoints, paletteColor } from "@/lib/charts/geometry";
import { ChartLegend } from "./ChartLegend";

/**
 * Inline-SVG line chart (no charting dependency). One polyline per series, points
 * evenly spaced across the category axis and scaled to the largest value.
 * Responsive viewBox; theme-aware axis via currentColor; point markers carry a
 * <title> for hover values.
 */
export function LineChart({
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

  const W = 480;
  const H = 260;
  const padL = 40;
  const padR = 12;
  const padT = 12;
  const padB = 44;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const max = maxValue(model);

  const ticks = 4;
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => (max / ticks) * i);
  const n = model.categories.length;

  if (n === 0 || max <= 0) {
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
        <title id={titleId}>{title ?? "Line chart"}</title>
        <desc id={descId}>{desc ?? `Line chart of ${model.series.map((s) => s.name).join(", ")}`}</desc>

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
        <line x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH} stroke="currentColor" strokeOpacity={0.3} />

        {model.series.map((s, si) => {
          const pts = linePoints(s.values, { plotWidth: plotW, plotHeight: plotH, max }).map((p) => ({
            x: padL + p.x,
            y: padT + p.y,
          }));
          const d = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" ");
          const color = paletteColor(si);
          return (
            <g key={si}>
              <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {pts.map((p, i) => (
                <circle key={i} cx={p.x} cy={p.y} r={2.5} fill={color}>
                  <title>{`${model.categories[i]} · ${s.name}: ${s.values[i]}`}</title>
                </circle>
              ))}
            </g>
          );
        })}

        {/* x labels (thin out when crowded) */}
        {model.categories.map((cat, i) => {
          const every = Math.ceil(n / 8);
          if (i % every !== 0 && i !== n - 1) return null;
          const x = n === 1 ? padL + plotW / 2 : padL + (i / (n - 1)) * plotW;
          return (
            <text
              key={i}
              x={x}
              y={padT + plotH + 14}
              textAnchor="middle"
              fontSize={9}
              fill="currentColor"
              fillOpacity={0.8}
            >
              {truncate(cat)}
            </text>
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

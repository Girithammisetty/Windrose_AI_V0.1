"use client";
import { useId } from "react";
import { toHeatmapModel } from "@/lib/charts/geometry";

/**
 * Inline-SVG heatmap grid (no charting dependency) — the bespoke renderer for
 * the heatmap family's simplest member, `heatmap_chart`. Shaped data is the
 * chart-service Shape() heatmap column order: [x, y, "value"]. Each (x,y) cell
 * is a rect whose fill opacity is proportional to its value; a <title> carries
 * the exact value for hover (a11y + tooltip), matching the other SVG renderers.
 */
export function HeatmapChart({
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
  const model = toHeatmapModel(columns, rows);
  const titleId = useId();
  const descId = useId();

  const cell = 34;
  const padL = 90;
  const padT = 12;
  const padB = 44;
  const padR = 12;
  const cols = Math.max(1, model.xCategories.length);
  const rowsN = Math.max(1, model.yCategories.length);
  const plotW = cols * cell;
  const plotH = rowsN * cell;
  const W = padL + plotW + padR;
  const H = padT + plotH + padB;

  if (model.cells.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">No values to plot.</p>;
  }

  return (
    <div className="w-full overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto text-muted-foreground"
        style={{ minWidth: Math.min(W, 480) }}
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
      >
        <title id={titleId}>{title ?? "Heat map"}</title>
        <desc id={descId}>{desc ?? `Heat map over ${model.xCategories.length} × ${model.yCategories.length} cells`}</desc>

        {model.yCategories.map((y, yi) => (
          <text
            key={`y-${yi}`}
            x={padL - 6}
            y={padT + yi * cell + cell / 2 + 3}
            textAnchor="end"
            fontSize={9}
            fill="currentColor"
            fillOpacity={0.8}
          >
            {truncate(y)}
          </text>
        ))}
        {model.xCategories.map((x, xi) => (
          <text
            key={`x-${xi}`}
            x={padL + xi * cell + cell / 2}
            y={padT + plotH + 14}
            textAnchor="middle"
            fontSize={9}
            fill="currentColor"
            fillOpacity={0.8}
          >
            {truncate(x)}
          </text>
        ))}

        {model.cells.map((c, i) => {
          const intensity = model.max > 0 ? Math.max(0.08, c.value / model.max) : 0.08;
          return (
            <rect
              key={i}
              x={padL + c.xi * cell + 1}
              y={padT + c.yi * cell + 1}
              width={cell - 2}
              height={cell - 2}
              rx={2}
              fill="hsl(211 90% 48%)"
              fillOpacity={intensity}
              stroke="currentColor"
              strokeOpacity={0.08}
            >
              <title>{`${model.xCategories[c.xi]} · ${model.yCategories[c.yi]}: ${c.value}`}</title>
            </rect>
          );
        })}
      </svg>
    </div>
  );
}

function truncate(s: string, n = 12): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

"use client";
import { toChartModel } from "@/lib/charts/geometry";
import { paletteColor } from "@/lib/charts/geometry";

/**
 * Word cloud (no charting/layout dependency) for `word_cloud_chart` (y_only
 * family). Real values drive real font sizes — no packing/collision-avoidance
 * layout algorithm (out of scope for time; a flex-wrap flow is an honest,
 * legible substitute that still reads the value at a glance).
 */
export function WordCloudChart({
  columns,
  rows,
  title,
}: {
  columns: unknown;
  rows: unknown;
  title?: string;
}) {
  const model = toChartModel(columns, rows);
  const values = model.series[0]?.values ?? [];
  const labels = model.categories.length ? model.categories : (model.series[0] ? [model.series[0].name] : []);

  if (values.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">No values to plot.</p>;
  }

  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const MIN_PX = 12;
  const MAX_PX = 40;

  return (
    <div
      role="img"
      aria-label={title ?? "Word cloud"}
      className="flex w-full flex-wrap items-baseline justify-center gap-x-3 gap-y-1 p-4"
    >
      {labels.map((label, i) => {
        const v = values[i] ?? 0;
        const frac = max > min ? (v - min) / (max - min) : 1;
        const px = MIN_PX + frac * (MAX_PX - MIN_PX);
        return (
          <span
            key={`${label}-${i}`}
            title={`${label}: ${v}`}
            style={{ fontSize: `${px}px`, color: paletteColor(i), lineHeight: 1.1 }}
            className="font-semibold"
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}

"use client";
import { paletteColor } from "@/lib/charts/geometry";

/** Small categorical legend shared by the multi-series SVG renderers. */
export function ChartLegend({ series }: { series: string[] }) {
  return (
    <ul className="mt-2 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {series.map((name, i) => (
        <li key={`${name}-${i}`} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block size-2.5 rounded-sm"
            style={{ backgroundColor: paletteColor(i) }}
          />
          <span className="truncate">{name}</span>
        </li>
      ))}
    </ul>
  );
}

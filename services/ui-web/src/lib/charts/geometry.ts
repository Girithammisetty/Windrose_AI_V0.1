/**
 * Pure data→geometry helpers for the inline-SVG chart renderers (no charting
 * dependency — mirrors the pipeline builder's "no react-flow" approach). Every
 * function here is framework-free so it is unit-testable in isolation against
 * the REAL ShapedResult shape: `columns` string[] + `rows` any[][].
 *
 *   columns = ["claim_type", "claim_count"]
 *   rows    = [["auto", 9], ["property", 3], ["health", 2]]
 *
 * For axis / y_only families col[0] is the category (x) label and col[1..] are
 * series values. Grid passes columns/rows straight through to the DataTable.
 */

/** Coerce a shaped cell into a finite number (non-numeric → 0). */
export function toNumber(v: unknown): number {
  if (typeof v === "number") return Number.isFinite(v) ? v : 0;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

/** Coerce a shaped cell into a display label. */
export function toLabel(v: unknown): string {
  if (v == null) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export interface Series {
  /** Column name (e.g. "claim_count"). */
  name: string;
  values: number[];
}

/** A normalized model for axis/y_only renderers: categories + one series per value column. */
export interface ChartModel {
  columns: string[];
  categories: string[];
  series: Series[];
}

function asColumns(columns: unknown): string[] {
  return Array.isArray(columns) ? columns.map(toLabel) : [];
}
function asRows(rows: unknown): unknown[][] {
  if (!Array.isArray(rows)) return [];
  return rows.map((r) => (Array.isArray(r) ? r : [r]));
}

/** True when every cell in column `idx` is numeric or a numeric string. An
 * empty row set counts as numeric (nothing to contradict it). */
function isNumericColumn(rows: unknown[][], idx: number): boolean {
  if (rows.length === 0) return true;
  return rows.every((r) => {
    const v = r[idx];
    if (typeof v === "number") return Number.isFinite(v);
    if (typeof v === "string") return v.trim() !== "" && Number.isFinite(Number(v));
    return false;
  });
}

/**
 * Build the category + series model from shaped columns/rows. col[0] is the
 * category axis.
 *
 * Three shapes are supported, auto-detected from the data:
 *  - WIDE (default): every subsequent column is its own numeric series, e.g.
 *    [x, sum_a, count_b] from picking several y-measures.
 *  - LONG/pivot: exactly 3 columns where col[1] is NOT numeric — the shape
 *    chart-service emits when an axis-family chart config sets `dataseries`
 *    (stack-by / series-split), e.g. [region, product_line, sum_revenue].
 *    Rows are pivoted so each distinct col[1] value becomes its own series.
 *  - SINGLE VALUE (y_only, no x picked): chart-service's Shape() declares 2
 *    column names (`["label", measureColName]`, see shape.go tabularColumns
 *    FamilyYOnly) even though the underlying query — no x dimension — only
 *    ever returns ONE cell per row, so `cols.length` overstates the real row
 *    width. Confirmed against a live gauge_chart preview: columns=["label",
 *    "sum_total_amount"], rows=[[117503.15]]. Detected by comparing declared
 *    column count against the actual row width, so col[1] isn't misread as a
 *    label and the real value doesn't get silently coerced to 0.
 */
export function toChartModel(columns: unknown, rows: unknown): ChartModel {
  const cols = asColumns(columns);
  const rws = asRows(rows);
  const rowWidth = rws.length > 0 ? rws[0].length : cols.length;

  if (cols.length > 1 && rowWidth === 1) {
    const name = cols[cols.length - 1] ?? cols[0];
    return {
      columns: cols,
      categories: rws.map(() => name),
      series: [{ name, values: rws.map((r) => toNumber(r[0])) }],
    };
  }
  if (cols.length === 3 && rws.length > 0 && !isNumericColumn(rws, 1)) {
    return pivotSeriesModel(cols, rws);
  }
  const categories = rws.map((r) => toLabel(r[0]));
  const series: Series[] = [];
  for (let c = 1; c < cols.length; c++) {
    series.push({ name: cols[c], values: rws.map((r) => toNumber(r[c])) });
  }
  // Single-column data (e.g. a lone value column) → treat it as one series.
  if (cols.length === 1 && rws.length > 0) {
    series.push({ name: cols[0], values: rws.map((r) => toNumber(r[0])) });
  }
  return { columns: cols, categories, series };
}

/** Pivot a [category, series, value] long-format table into wide series
 * (one Series per distinct col[1] value), preserving first-seen order. */
function pivotSeriesModel(cols: string[], rws: unknown[][]): ChartModel {
  const categories: string[] = [];
  const catIndex = new Map<string, number>();
  const seriesNames: string[] = [];
  const seriesIndex = new Map<string, number>();
  for (const r of rws) {
    const cat = toLabel(r[0]);
    const ser = toLabel(r[1]);
    if (!catIndex.has(cat)) {
      catIndex.set(cat, categories.length);
      categories.push(cat);
    }
    if (!seriesIndex.has(ser)) {
      seriesIndex.set(ser, seriesNames.length);
      seriesNames.push(ser);
    }
  }
  const values: number[][] = seriesNames.map(() => categories.map(() => 0));
  for (const r of rws) {
    const ci = catIndex.get(toLabel(r[0]))!;
    const si = seriesIndex.get(toLabel(r[1]))!;
    values[si][ci] = toNumber(r[2]);
  }
  const series: Series[] = seriesNames.map((name, i) => ({ name, values: values[i] }));
  return { columns: cols, categories, series };
}

/** The maximum series value across a model (>= 0), used as the axis top. */
export function maxValue(model: ChartModel): number {
  let m = 0;
  for (const s of model.series) for (const v of s.values) if (v > m) m = v;
  return m;
}

/**
 * Scale raw values to pixel heights proportional to the largest value: the max
 * value maps to `plotHeight`, zero maps to zero. A flat/empty set maps to zero
 * heights (nothing to draw). Returns one height per input value.
 */
export function scaleToHeight(values: number[], plotHeight: number): number[] {
  const max = values.reduce((m, v) => (v > m ? v : m), 0);
  if (max <= 0) return values.map(() => 0);
  return values.map((v) => (Math.max(0, v) / max) * plotHeight);
}

export interface PieSlice {
  value: number;
  fraction: number;
  /** Degrees, clockwise from 12 o'clock. */
  startAngle: number;
  endAngle: number;
}

/**
 * Split values into pie slices whose sweep angles are proportional to value and
 * sum to 360°. An all-zero/empty set yields no slices.
 */
export function pieSlices(values: number[]): PieSlice[] {
  const total = values.reduce((s, v) => s + Math.max(0, v), 0);
  if (total <= 0) return [];
  const out: PieSlice[] = [];
  let cursor = 0;
  for (const v of values) {
    const fraction = Math.max(0, v) / total;
    const sweep = fraction * 360;
    out.push({ value: v, fraction, startAngle: cursor, endAngle: cursor + sweep });
    cursor += sweep;
  }
  return out;
}

/** Point on a circle for `angle` degrees measured clockwise from 12 o'clock. */
export function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number): { x: number; y: number } {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

/** SVG path for a pie wedge from center out to the arc between two angles. */
export function arcPath(cx: number, cy: number, r: number, startAngle: number, endAngle: number): string {
  // A single 360° slice can't be drawn as one arc (start == end); nudge it.
  const sweep = endAngle - startAngle;
  if (sweep >= 359.999) {
    const mid = startAngle + 180;
    return [arcPath(cx, cy, r, startAngle, mid), arcPath(cx, cy, r, mid, endAngle)].join(" ");
  }
  const start = polarToCartesian(cx, cy, r, startAngle);
  const end = polarToCartesian(cx, cy, r, endAngle);
  const largeArc = sweep > 180 ? 1 : 0;
  return [
    `M ${cx} ${cy}`,
    `L ${start.x.toFixed(3)} ${start.y.toFixed(3)}`,
    `A ${r} ${r} 0 ${largeArc} 1 ${end.x.toFixed(3)} ${end.y.toFixed(3)}`,
    "Z",
  ].join(" ");
}

/** Polyline points for a line series, evenly spaced across `plotWidth`. */
export function linePoints(
  values: number[],
  opts: { plotWidth: number; plotHeight: number; max: number },
): { x: number; y: number }[] {
  const { plotWidth, plotHeight, max } = opts;
  const n = values.length;
  if (n === 0) return [];
  const step = n === 1 ? 0 : plotWidth / (n - 1);
  return values.map((v, i) => ({
    x: n === 1 ? plotWidth / 2 : i * step,
    y: max <= 0 ? plotHeight : plotHeight - (Math.max(0, v) / max) * plotHeight,
  }));
}

/**
 * A small, accessible categorical palette (colour-blind-safe ordering). Rendered
 * as CSS custom colours so charts stay theme-aware; series index wraps around.
 */
export const CHART_PALETTE = [
  "hsl(211 90% 48%)", // blue
  "hsl(150 60% 40%)", // green
  "hsl(35 90% 50%)", // amber
  "hsl(280 55% 55%)", // violet
  "hsl(0 72% 55%)", // red
  "hsl(190 70% 42%)", // cyan
] as const;

export function paletteColor(i: number): string {
  return CHART_PALETTE[i % CHART_PALETTE.length];
}

export interface HeatmapCell {
  xi: number;
  yi: number;
  value: number;
}

/** A normalized model for the heatmap-family renderer: distinct x/y category
 * lists plus one cell per (x,y) pair present in the shaped rows. Mirrors
 * chart-service's heatmap Shape() column order: [x, y, "value"]. */
export interface HeatmapModel {
  xCategories: string[];
  yCategories: string[];
  cells: HeatmapCell[];
  max: number;
}

/** Build the heatmap grid model from shaped [x, y, value] rows. `columns` is
 * accepted (unused) to mirror toChartModel's signature — heatmap's Shape()
 * column order is fixed ([x, y, "value"]), so cells are read by position. */
export function toHeatmapModel(_columns: unknown, rows: unknown): HeatmapModel {
  const rws = asRows(rows);
  const xCategories: string[] = [];
  const xIndex = new Map<string, number>();
  const yCategories: string[] = [];
  const yIndex = new Map<string, number>();
  const cells: HeatmapCell[] = [];
  let max = 0;
  for (const r of rws) {
    const x = toLabel(r[0]);
    const y = toLabel(r[1]);
    const v = toNumber(r[2]);
    if (!xIndex.has(x)) {
      xIndex.set(x, xCategories.length);
      xCategories.push(x);
    }
    if (!yIndex.has(y)) {
      yIndex.set(y, yCategories.length);
      yCategories.push(y);
    }
    cells.push({ xi: xIndex.get(x)!, yi: yIndex.get(y)!, value: v });
    if (v > max) max = v;
  }
  return { xCategories, yCategories, cells, max };
}

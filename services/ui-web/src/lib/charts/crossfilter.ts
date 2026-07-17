/**
 * Dashboard cross-filtering (CHART-FR-041). A click on a chart mark (bar / pie
 * slice / grid row) emits a CrossFilter: the clicked category becomes an `eq`
 * predicate on that chart's group-by dimension, tagged with the source chart's
 * id (`origin`). The dashboard sends the active set to chart-service's batch
 * endpoint, which applies each predicate to the origin's same-model siblings and
 * never to the origin chart itself — so the source chart keeps its full view
 * while the rest of the board filters.
 *
 * Selection is single-value per origin chart: clicking a second category on the
 * same chart replaces its predicate; clicking the active one clears it (toggle).
 */
export interface CrossFilter {
  /** The group-by dimension being filtered (a dimension name in the shared model). */
  field: string;
  /** Only `eq` is emitted from a mark click today (chart-service supports more). */
  op: "eq";
  /** The clicked category value (bind parameter, never interpolated downstream). */
  value: string;
  /** The chart id whose selection produced this predicate. */
  origin: string;
}

/** The wire shape sent as the `filters` GraphQL variable (matches ChartFilterInput). */
export interface CrossFilterVar {
  field: string;
  op: string;
  value: unknown;
  origin?: string;
}

/**
 * Derive the group-by dimension name a chart's clicks filter on. Prefer the
 * authored `config.x.dimension`; fall back to the first shaped column, whose
 * header is the dimension name in aggregated results. Returns null when neither
 * is available (chart cannot participate as a cross-filter source).
 */
export function crossFilterField(config: unknown, columns: unknown): string | null {
  const cfg = config as { x?: { dimension?: unknown }; dimension?: unknown } | null | undefined;
  const dim = cfg?.x?.dimension ?? cfg?.dimension;
  if (typeof dim === "string" && dim) return dim;
  if (Array.isArray(columns) && columns.length > 0 && columns[0] != null) return String(columns[0]);
  return null;
}

/** The currently-selected value for a given origin chart, or null. */
export function selectedValueFor(filters: CrossFilter[], origin: string): string | null {
  return filters.find((f) => f.origin === origin)?.value ?? null;
}

/**
 * Toggle a selection for one origin chart: clear if the same value is re-clicked,
 * otherwise replace any existing predicate from that origin with the new one.
 */
export function toggleCrossFilter(
  filters: CrossFilter[],
  origin: string,
  field: string,
  value: string,
): CrossFilter[] {
  const existing = filters.find((f) => f.origin === origin);
  const rest = filters.filter((f) => f.origin !== origin);
  if (existing && existing.value === value) return rest; // re-click clears
  return [...rest, { field, op: "eq", value, origin }];
}

/** Map the active selections to the GraphQL `filters` variable (undefined if empty). */
export function toFilterVars(filters: CrossFilter[]): CrossFilterVar[] | undefined {
  return filters.length ? filters.map((f) => ({ field: f.field, op: f.op, value: f.value, origin: f.origin })) : undefined;
}

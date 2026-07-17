/**
 * JSON-aware semantic diff (UI-FR-019 <DiffView>). Pure functions, fully unit
 * tested. Produces a flat list of leaf changes keyed by dotted JSON path, plus a
 * unified line model. The argsDiff from a proposal may arrive either as an
 * already-computed {before,after} pair or as two arg objects; both are handled.
 */

export type ChangeKind = "added" | "removed" | "changed" | "unchanged";

export interface DiffLeaf {
  path: string;
  kind: ChangeKind;
  before?: unknown;
  after?: unknown;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function stable(v: unknown): string {
  if (isObject(v)) {
    return `{${Object.keys(v)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable(v[k])}`)
      .join(",")}}`;
  }
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  return JSON.stringify(v);
}

/** Deep semantic diff of two JSON values → leaf changes. */
export function diffJson(before: unknown, after: unknown, base = ""): DiffLeaf[] {
  // Both leaves (or arrays / mismatched types): compare by stable serialization.
  if (!isObject(before) || !isObject(after)) {
    if (stable(before) === stable(after)) {
      return [{ path: base || "$", kind: "unchanged", before, after }];
    }
    if (before === undefined) return [{ path: base || "$", kind: "added", after }];
    if (after === undefined) return [{ path: base || "$", kind: "removed", before }];
    return [{ path: base || "$", kind: "changed", before, after }];
  }

  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
  const out: DiffLeaf[] = [];
  for (const k of keys) {
    const path = base ? `${base}.${k}` : k;
    const b = before[k];
    const a = after[k];
    if (!(k in before)) out.push({ path, kind: "added", after: a });
    else if (!(k in after)) out.push({ path, kind: "removed", before: b });
    else out.push(...diffJson(b, a, path));
  }
  return out;
}

export interface NormalizedDiff {
  before: unknown;
  after: unknown;
}

/**
 * Normalize a proposal's `argsDiff` into {before, after}. Accepts:
 *  - { before, after }
 *  - { current, proposed }
 *  - { old, new }
 *  - a bare proposed-args object (before treated as {}).
 */
export function normalizeArgsDiff(argsDiff: unknown): NormalizedDiff {
  if (isObject(argsDiff)) {
    const d = argsDiff as Record<string, unknown>;
    if ("before" in d || "after" in d) return { before: d.before ?? {}, after: d.after ?? {} };
    if ("current" in d || "proposed" in d) return { before: d.current ?? {}, after: d.proposed ?? {} };
    if ("old" in d || "new" in d) return { before: d.old ?? {}, after: d.new ?? {} };
  }
  return { before: {}, after: argsDiff ?? {} };
}

export function changedLeaves(argsDiff: unknown): DiffLeaf[] {
  const { before, after } = normalizeArgsDiff(argsDiff);
  return diffJson(before, after).filter((l) => l.kind !== "unchanged");
}

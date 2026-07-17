/**
 * Agent-run trace normalization (UI-FR-034). The BFF passes AgentRun.trace as
 * opaque JSON; this flattens an arbitrary tool-call tree into an ordered, indented
 * row list suitable for virtualization (AC-7: 800 nodes render fast). Pure +
 * unit tested. Error nodes are marked so the UI can auto-expand them.
 */

export interface RawTraceNode {
  id?: string;
  span_id?: string;
  name?: string;
  type?: string; // agent | step | tool_call | sub_agent
  status?: string; // ok | error | running | ...
  durationMs?: number;
  duration_ms?: number;
  tokens?: number;
  costUsd?: number;
  cost_usd?: number;
  error?: unknown;
  citations?: { urn: string; label?: string }[];
  children?: RawTraceNode[];
  steps?: RawTraceNode[];
  calls?: RawTraceNode[];
}

export interface TraceRow {
  id: string;
  depth: number;
  name: string;
  type: string;
  status: string;
  durationMs?: number;
  tokens?: number;
  costUsd?: number;
  isError: boolean;
  hasChildren: boolean;
  citations: { urn: string; label?: string }[];
  error?: unknown;
}

function childrenOf(n: RawTraceNode): RawTraceNode[] {
  return [...(n.children ?? []), ...(n.steps ?? []), ...(n.calls ?? [])];
}

function rootsOf(trace: unknown): RawTraceNode[] {
  if (Array.isArray(trace)) return trace as RawTraceNode[];
  if (trace && typeof trace === "object") {
    const t = trace as RawTraceNode & { root?: RawTraceNode; spans?: RawTraceNode[] };
    if (t.root) return [t.root];
    if (Array.isArray(t.spans)) return t.spans;
    return [t];
  }
  return [];
}

let counter = 0;
function idFor(n: RawTraceNode): string {
  return n.id ?? n.span_id ?? `span-${counter++}`;
}

/**
 * Flatten a trace into rows honoring `expanded` (a set of node ids). A node's
 * children are emitted only when the node is expanded OR the node is an ancestor
 * of an error node (errors are auto-expanded, AC-7).
 */
export function flattenTrace(trace: unknown, expanded: Set<string>): TraceRow[] {
  counter = 0;
  const out: TraceRow[] = [];

  const hasErrorDescendant = (n: RawTraceNode): boolean => {
    if (isErrorStatus(n.status)) return true;
    return childrenOf(n).some(hasErrorDescendant);
  };

  const walk = (n: RawTraceNode, depth: number) => {
    const kids = childrenOf(n);
    const id = idFor(n);
    const isError = isErrorStatus(n.status);
    out.push({
      id,
      depth,
      name: n.name ?? n.type ?? "span",
      type: n.type ?? "span",
      status: n.status ?? "ok",
      durationMs: n.durationMs ?? n.duration_ms,
      tokens: n.tokens,
      costUsd: n.costUsd ?? n.cost_usd,
      isError,
      hasChildren: kids.length > 0,
      citations: n.citations ?? [],
      error: n.error,
    });
    const autoExpand = kids.some(hasErrorDescendant) || kids.some((k) => isErrorStatus(k.status));
    if (kids.length > 0 && (expanded.has(id) || autoExpand)) {
      for (const k of kids) walk(k, depth + 1);
    }
  };

  for (const r of rootsOf(trace)) walk(r, 0);
  return out;
}

export function isErrorStatus(status?: string): boolean {
  return !!status && ["error", "failed", "failure"].includes(status.toLowerCase());
}

/** Collect the ids that must be expanded to reveal a given span (deep-link, AC-7). */
export function pathToSpan(trace: unknown, spanId: string): string[] {
  const path: string[] = [];
  const walk = (n: RawTraceNode, acc: string[]): boolean => {
    const id = idFor(n);
    const next = [...acc, id];
    if (id === spanId) {
      path.push(...acc);
      return true;
    }
    for (const k of childrenOf(n)) if (walk(k, next)) return true;
    return false;
  };
  counter = 0;
  for (const r of rootsOf(trace)) if (walk(r, [])) break;
  return path;
}

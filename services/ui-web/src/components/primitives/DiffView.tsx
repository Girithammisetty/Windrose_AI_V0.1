"use client";
import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { diffJson, normalizeArgsDiff, type DiffLeaf } from "@/lib/diff";

function render(v: unknown): string {
  if (v === undefined) return "∅";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 0);
}

const KIND_STYLE: Record<DiffLeaf["kind"], string> = {
  added: "bg-[hsl(var(--success))]/15 text-foreground",
  removed: "bg-destructive/15 text-foreground line-through/0",
  changed: "bg-[hsl(var(--warning))]/15 text-foreground",
  unchanged: "text-muted-foreground",
};

/**
 * JSON-aware semantic diff view (UI-FR-019). Side-by-side or unified; a11y
 * annotated. Consumes a proposal's argsDiff (before/current/proposed shapes all
 * normalized). Edited args are highlighted the same way (changed rows).
 */
export function DiffView({
  argsDiff,
  className,
  defaultMode = "unified",
}: {
  argsDiff: unknown;
  className?: string;
  defaultMode?: "unified" | "split";
}) {
  const [mode, setMode] = useState<"unified" | "split">(defaultMode);
  const { before, after } = useMemo(() => normalizeArgsDiff(argsDiff), [argsDiff]);
  const leaves = useMemo(() => diffJson(before, after), [before, after]);

  return (
    <div className={cn("rounded-md border", className)} data-diff-view="true">
      <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">Proposed change</span>
        <div className="flex gap-1" role="tablist" aria-label="diff mode">
          {(["unified", "split"] as const).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={cn(
                "rounded px-2 py-0.5 text-xs capitalize",
                mode === m ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </div>
      <div className="max-h-72 overflow-auto p-2 font-mono text-xs">
        {leaves.length === 0 && <p className="p-2 text-muted-foreground">No changes.</p>}
        {mode === "unified"
          ? leaves.map((l) => <UnifiedRow key={l.path} leaf={l} />)
          : leaves.map((l) => <SplitRow key={l.path} leaf={l} />)}
      </div>
    </div>
  );
}

function UnifiedRow({ leaf }: { leaf: DiffLeaf }) {
  if (leaf.kind === "unchanged") {
    return (
      <div className="flex gap-2 px-1 text-muted-foreground">
        <span className="w-40 shrink-0 truncate">{leaf.path}</span>
        <span className="truncate">{render(leaf.after)}</span>
      </div>
    );
  }
  return (
    <div className="px-1" aria-label={`${leaf.kind} ${leaf.path}`}>
      {(leaf.kind === "removed" || leaf.kind === "changed") && (
        <div className={cn("flex gap-2", "bg-destructive/15")}>
          <span className="w-4 shrink-0 text-destructive">−</span>
          <span className="w-40 shrink-0 truncate">{leaf.path}</span>
          <span className="truncate">{render(leaf.before)}</span>
        </div>
      )}
      {(leaf.kind === "added" || leaf.kind === "changed") && (
        <div className={cn("flex gap-2", "bg-[hsl(var(--success))]/15")}>
          <span className="w-4 shrink-0 text-[hsl(var(--success))]">+</span>
          <span className="w-40 shrink-0 truncate">{leaf.path}</span>
          <span className="truncate">{render(leaf.after)}</span>
        </div>
      )}
    </div>
  );
}

function SplitRow({ leaf }: { leaf: DiffLeaf }) {
  return (
    <div className={cn("grid grid-cols-[10rem_1fr_1fr] gap-2 px-1", KIND_STYLE[leaf.kind])}>
      <span className="truncate">{leaf.path}</span>
      <span className="truncate">{render(leaf.before)}</span>
      <span className="truncate">{render(leaf.after)}</span>
    </div>
  );
}

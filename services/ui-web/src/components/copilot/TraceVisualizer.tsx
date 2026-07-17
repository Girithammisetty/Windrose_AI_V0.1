"use client";
import { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronRight, ChevronDown, AlertCircle, Link2 } from "lucide-react";
import { flattenTrace, isErrorStatus } from "@/lib/trace";
import { UrnLink } from "@/components/primitives/UrnLink";
import { cn, formatUsd } from "@/lib/utils";

/**
 * Agent-run trace visualizer (UI-FR-034, AC-7). Collapsible tool-call tree,
 * per-node status/duration/token cost, citations, error nodes auto-expanded.
 * Virtualized (< 100 DOM rows over 800+ node traces). Each span has a deep link
 * that reproduces its expanded state via the ?span= URL param.
 */
export function TraceVisualizer({ trace }: { trace: unknown }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const parentRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => flattenTrace(trace, expanded), [trace, expanded]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 34,
    overscan: 15,
  });

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (rows.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No trace recorded for this run.</p>;
  }

  return (
    <div className="rounded-lg border" role="tree" aria-label="Agent run trace">
      <div ref={parentRef} className="max-h-[60vh] overflow-auto">
        <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
          {virtualizer.getVirtualItems().map((vi) => {
            const row = rows[vi.index];
            const isOpen = expanded.has(row.id) || row.isError;
            return (
              <div
                key={row.id}
                role="treeitem"
                aria-level={row.depth + 1}
                aria-selected={false}
                aria-expanded={row.hasChildren ? isOpen : undefined}
                data-span-id={row.id}
                data-error={row.isError ? "true" : "false"}
                className={cn(
                  "absolute left-0 flex w-full items-center gap-2 border-b px-2 text-sm",
                  row.isError && "bg-destructive/10",
                )}
                style={{
                  height: vi.size,
                  transform: `translateY(${vi.start}px)`,
                  paddingLeft: `${row.depth * 18 + 8}px`,
                }}
              >
                {row.hasChildren ? (
                  <button aria-label={isOpen ? "Collapse" : "Expand"} onClick={() => toggle(row.id)}>
                    {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                  </button>
                ) : (
                  <span className="w-3.5" />
                )}
                {row.isError && <AlertCircle className="size-3.5 shrink-0 text-destructive" aria-hidden />}
                <span className="truncate font-medium">{row.name}</span>
                <span className="text-xs text-muted-foreground">{row.type}</span>
                <span
                  className={cn(
                    "rounded px-1 text-[10px]",
                    isErrorStatus(row.status) ? "bg-destructive/20 text-destructive" : "bg-muted text-muted-foreground",
                  )}
                >
                  {row.status}
                </span>
                <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                  {row.durationMs != null && <span>{row.durationMs}ms</span>}
                  {row.tokens != null && <span>{row.tokens}tok</span>}
                  {row.costUsd != null && <span>{formatUsd(row.costUsd)}</span>}
                  <button
                    aria-label="Copy span deep link"
                    title="Copy span deep link"
                    onClick={() => {
                      const url = `${window.location.pathname}?span=${row.id}`;
                      void navigator.clipboard?.writeText(window.location.origin + url);
                    }}
                  >
                    <Link2 className="size-3" />
                  </button>
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Citations for error rows shown below, expanded by default */}
      {rows
        .filter((r) => r.isError && (r.citations.length > 0 || r.error != null))
        .map((r) => (
          <div key={`err-${r.id}`} className="border-t bg-destructive/5 p-3 text-xs">
            <p className="font-semibold text-destructive">Error in {r.name}</p>
            {r.error != null && (
              <pre className="mt-1 overflow-auto rounded bg-background p-2 font-mono">
                {typeof r.error === "string" ? r.error : JSON.stringify(r.error, null, 2)}
              </pre>
            )}
            {r.citations.map((c, i) => (
              <UrnLink key={i} urn={c.urn} label={c.label} className="mt-1" />
            ))}
          </div>
        ))}
    </div>
  );
}

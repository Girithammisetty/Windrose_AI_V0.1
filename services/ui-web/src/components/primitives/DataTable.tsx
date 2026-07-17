"use client";
import { useRef, useCallback, useEffect } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/primitives";

export interface Column<T> {
  id: string;
  /** Column header — a plain string, or a node (e.g. an interactive sort
   * toggle). Rendered inside the sticky header cell. */
  header: React.ReactNode;
  width?: number | string;
  cell: (row: T) => React.ReactNode;
  className?: string;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: Column<T>[];
  rowId: (row: T) => string;
  /** Cursor infinite-load (UI-FR-011): called near the bottom when hasMore. */
  hasMore?: boolean;
  isFetchingMore?: boolean;
  onLoadMore?: () => void;
  onRowActivate?: (row: T) => void;
  /** Selection (bulk ops). Controlled via selectedIds + onToggle. */
  selectable?: boolean;
  selectedIds?: Set<string>;
  onToggle?: (id: string) => void;
  estimateRowHeight?: number;
  ariaLabel: string;
  emptyState?: React.ReactNode;
}

/**
 * The ONLY sanctioned table primitive (UI-FR-019): windowed virtualization
 * (< 100 DOM rows over 1M-row sets, AC-2), sticky header, cursor infinite-load,
 * keyboard row navigation, and ARIA grid semantics. Screens never hand-roll a
 * `.map` into <tr> — they use this.
 */
export function DataTable<T>({
  rows,
  columns,
  rowId,
  hasMore,
  isFetchingMore,
  onLoadMore,
  onRowActivate,
  selectable,
  selectedIds,
  onToggle,
  estimateRowHeight = 44,
  ariaLabel,
  emptyState,
}: DataTableProps<T>) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => estimateRowHeight,
    overscan: 12,
  });

  // Cursor infinite-load: fetch more when the last window item nears the end.
  const items = virtualizer.getVirtualItems();
  useEffect(() => {
    const last = items[items.length - 1];
    if (!last) return;
    if (hasMore && !isFetchingMore && last.index >= rows.length - 8) {
      onLoadMore?.();
    }
  }, [items, hasMore, isFetchingMore, rows.length, onLoadMore]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement;
      const idxAttr = target.getAttribute("data-row-index");
      if (idxAttr == null) return;
      const idx = Number(idxAttr);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = e.key === "ArrowDown" ? Math.min(rows.length - 1, idx + 1) : Math.max(0, idx - 1);
        virtualizer.scrollToIndex(next);
        requestAnimationFrame(() => {
          parentRef.current?.querySelector<HTMLElement>(`[data-row-index="${next}"]`)?.focus();
        });
      } else if (e.key === "Enter" && onRowActivate) {
        e.preventDefault();
        onRowActivate(rows[idx]);
      } else if (e.key === " " && selectable && onToggle) {
        e.preventDefault();
        onToggle(rowId(rows[idx]));
      }
    },
    [rows, virtualizer, onRowActivate, selectable, onToggle, rowId],
  );

  const gridCols = `${selectable ? "2.5rem " : ""}${columns.map((c) => (typeof c.width === "number" ? `${c.width}px` : c.width ?? "1fr")).join(" ")}`;

  if (rows.length === 0 && emptyState) return <>{emptyState}</>;

  return (
    <div className="rounded-lg border" role="grid" aria-label={ariaLabel} aria-rowcount={rows.length}>
      {/* Sticky header */}
      <div
        role="row"
        className="sticky top-0 z-10 grid items-center border-b bg-muted/60 px-2 text-xs font-medium text-muted-foreground backdrop-blur"
        style={{ gridTemplateColumns: gridCols }}
      >
        {selectable && <span role="columnheader" aria-label="select" />}
        {columns.map((c) => (
          <span key={c.id} role="columnheader" className={cn("truncate py-2", c.className)}>
            {c.header}
          </span>
        ))}
      </div>

      <div
        ref={parentRef}
        className="max-h-[calc(100vh-16rem)] overflow-auto"
        onKeyDown={onKeyDown}
        tabIndex={-1}
      >
        <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
          {items.map((vi) => {
            const row = rows[vi.index];
            const id = rowId(row);
            const selected = selectedIds?.has(id) ?? false;
            return (
              <div
                key={id}
                role="row"
                aria-rowindex={vi.index + 1}
                aria-selected={selectable ? selected : undefined}
                data-row-index={vi.index}
                data-row-id={id}
                tabIndex={0}
                onClick={() => onRowActivate?.(row)}
                className={cn(
                  "absolute left-0 grid w-full cursor-pointer items-center border-b px-2 text-sm hover:bg-accent/50 focus-visible:bg-accent",
                  selected && "bg-primary/10",
                )}
                style={{
                  gridTemplateColumns: gridCols,
                  height: vi.size,
                  transform: `translateY(${vi.start}px)`,
                }}
              >
                {selectable && (
                  <span role="gridcell" className="flex items-center">
                    <input
                      type="checkbox"
                      checked={selected}
                      aria-label={`Select ${id}`}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => onToggle?.(id)}
                      className="size-4 accent-[hsl(var(--primary))]"
                    />
                  </span>
                )}
                {columns.map((c) => (
                  <span
                    key={c.id}
                    role="gridcell"
                    className={cn("truncate py-2 pr-2", c.className)}
                  >
                    {c.cell(row)}
                  </span>
                ))}
              </div>
            );
          })}
        </div>
        {isFetchingMore && (
          <div className="space-y-1 p-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}
      </div>
    </div>
  );
}

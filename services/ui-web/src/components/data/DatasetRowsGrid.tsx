"use client";
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useDatasetRows } from "@/lib/graphql/hooks";
import type { CaseRowInput, RowFilterInput } from "@/lib/graphql/types";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { cap } from "@/lib/authz/registry";
import { CreateCasesDialog } from "@/components/cases/CreateCasesDialog";
import { formatNumber } from "@/lib/utils";

const PAGE_SIZES = [25, 50, 100, 200] as const;
type SortDir = "asc" | "desc";

interface GridRow {
  __id: string;
  cells: (string | null)[];
}

/**
 * Server-paged, sortable, per-column-filterable browse of a dataset's rows
 * (DST-FR-050). All paging/sort/filter happens server-side via `datasetRows`;
 * this component only holds the control state and renders the current page.
 * The header shows the total row count and, when a filter is active, how many
 * of them matched.
 */
export function DatasetRowsGrid({
  datasetId,
  datasetUrn,
}: {
  datasetId: string;
  /** Passed through to case creation as the row-anchor URN. When absent, the
   * "Create cases" worklist action is hidden (browse-only). */
  datasetUrn?: string;
}) {
  const [pageSize, setPageSize] = useState<number>(50);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<{ col: string; dir: SortDir } | null>(null);
  // Draft filter inputs (as typed) and the applied filters (debounced on submit).
  const [filterDraft, setFilterDraft] = useState<Record<string, string>>({});
  const [applied, setApplied] = useState<RowFilterInput[]>([]);
  // Selected rows for case creation, keyed by their stable row key (first
  // column value) so a selection survives paging. The value is the full
  // case-row (rowPk + projection) ready to submit.
  const [selected, setSelected] = useState<Map<string, CaseRowInput>>(new Map());
  const [caseDialogOpen, setCaseDialogOpen] = useState(false);

  const { data, isFetching, isError, error } = useDatasetRows(datasetId, {
    offset,
    limit: pageSize,
    sort: sort?.col ?? null,
    dir: sort?.dir ?? null,
    filters: applied,
  });

  const page = data?.datasetRows;
  const columns = page?.columns ?? [];
  const total = page?.total ?? 0;
  const filtered = page?.filtered ?? 0;
  const hasFilter = applied.length > 0;

  const gridRows: GridRow[] = useMemo(
    () => (page?.rows ?? []).map((r, i) => ({ __id: `${offset + i}`, cells: r })),
    [page?.rows, offset],
  );

  // The stable row key for selection/dedup = the first column's value (usually
  // an id). Falls back to the page position when the first cell is empty.
  const rowKeyOf = (row: GridRow): string =>
    row.cells[0] != null && row.cells[0] !== "" ? String(row.cells[0]) : row.__id;

  const caseRowOf = (row: GridRow): CaseRowInput => ({
    rowPk: rowKeyOf(row),
    displayProjection: columns.map((name, ci) => ({
      key: name,
      value: row.cells[ci] ?? "",
    })),
  });

  // DataTable selection is by __id; map the current page's selected __ids from
  // the persistent rowKey-keyed selection set.
  const selectedPageIds = useMemo(() => {
    const ids = new Set<string>();
    for (const r of gridRows) if (selected.has(rowKeyOf(r))) ids.add(r.__id);
    return ids;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridRows, selected]);

  function toggleRow(id: string) {
    const row = gridRows.find((r) => r.__id === id);
    if (!row) return;
    const key = rowKeyOf(row);
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(key)) next.delete(key);
      else next.set(key, caseRowOf(row));
      return next;
    });
  }

  function toggleSort(col: string) {
    setOffset(0);
    setSort((prev) => {
      if (prev?.col !== col) return { col, dir: "asc" };
      if (prev.dir === "asc") return { col, dir: "desc" };
      return null; // third click clears sort
    });
  }

  function applyFilters() {
    const next: RowFilterInput[] = Object.entries(filterDraft)
      .filter(([, v]) => v.trim() !== "")
      .map(([col, value]) => ({ col, op: "contains", value: value.trim() }));
    setOffset(0);
    setApplied(next);
  }

  function clearFilters() {
    setFilterDraft({});
    setApplied([]);
    setOffset(0);
  }

  const tableCols: Column<GridRow>[] = useMemo(
    () =>
      columns.map((name, ci) => ({
        id: `c${ci}`,
        header: (
          <button
            type="button"
            onClick={() => toggleSort(name)}
            className="flex items-center gap-1 font-medium hover:text-foreground"
            title={`Sort by ${name}`}
          >
            {name}
            {sort?.col === name ? (
              sort.dir === "asc" ? (
                <ArrowUp className="size-3" />
              ) : (
                <ArrowDown className="size-3" />
              )
            ) : (
              <ChevronsUpDown className="size-3 opacity-40" />
            )}
          </button>
        ),
        cell: (row: GridRow) => (
          <span className="font-mono text-xs">
            {row.cells[ci] === null ? (
              <span className="text-muted-foreground">—</span>
            ) : (
              row.cells[ci]
            )}
          </span>
        ),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [columns, sort],
  );

  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = offset + gridRows.length;
  const canPrev = offset > 0;
  const canNext = offset + pageSize < filtered;

  return (
    <div className="space-y-3">
      {/* header: counts + page size */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <div className="text-muted-foreground">
          {hasFilter ? (
            <span>
              <span className="font-medium text-foreground">{formatNumber(filtered)}</span>{" "}
              of {formatNumber(total)} rows match
            </span>
          ) : (
            <span>
              <span className="font-medium text-foreground">{formatNumber(total)}</span> rows
            </span>
          )}
          {isFetching && <span className="ml-2 text-xs">updating…</span>}
          {page?.truncated && (
            <span
              className="ml-2 text-xs text-amber-600 dark:text-amber-500"
              title="This dataset exceeds the browse limit; counts and sorting cover the first rows only."
            >
              · first {formatNumber(total)} rows
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {datasetUrn && selected.size > 0 && (
            <Can gate={cap("case.case.create")}>
              <Button type="button" size="sm" onClick={() => setCaseDialogOpen(true)}>
                Create {selected.size} case{selected.size === 1 ? "" : "s"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Map())}
              >
                Clear
              </Button>
            </Can>
          )}
          <label htmlFor="grid-page-size" className="text-xs text-muted-foreground">
            Rows per page
          </label>
          <select
            id="grid-page-size"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setOffset(0);
            }}
            className="rounded-md border bg-background px-2 py-1 text-sm"
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* per-column filter inputs */}
      {columns.length > 0 && (
        <div className="flex flex-wrap items-end gap-2 rounded-md border bg-muted/30 p-2">
          {columns.map((name) => (
            <div key={name} className="flex flex-col gap-0.5">
              <label htmlFor={`f-${name}`} className="text-[0.7rem] text-muted-foreground">
                {name}
              </label>
              <Input
                id={`f-${name}`}
                value={filterDraft[name] ?? ""}
                onChange={(e) =>
                  setFilterDraft((d) => ({ ...d, [name]: e.target.value }))
                }
                onKeyDown={(e) => e.key === "Enter" && applyFilters()}
                placeholder="filter…"
                className="h-7 w-32 text-xs"
              />
            </div>
          ))}
          <div className="flex gap-1">
            <Button type="button" size="sm" onClick={applyFilters}>
              Apply
            </Button>
            {hasFilter && (
              <Button type="button" size="sm" variant="ghost" onClick={clearFilters}>
                Clear
              </Button>
            )}
          </div>
        </div>
      )}

      {isError ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          Could not load rows: {(error as Error)?.message ?? "unknown error"}
        </p>
      ) : (
        <DataTable
          ariaLabel="Dataset rows"
          rows={gridRows}
          columns={tableCols}
          rowId={(r) => r.__id}
          estimateRowHeight={34}
          selectable={!!datasetUrn}
          selectedIds={selectedPageIds}
          onToggle={toggleRow}
          emptyState={
            <p className="p-6 text-center text-xs text-muted-foreground">
              {hasFilter ? "No rows match the current filters." : "No rows."}
            </p>
          }
        />
      )}

      {datasetUrn && (
        <CreateCasesDialog
          open={caseDialogOpen}
          onOpenChange={(o) => {
            setCaseDialogOpen(o);
            // Clear the selection once the dialog closes (after the user sees
            // the created/deduplicated summary), not the moment cases are made.
            if (!o) setSelected(new Map());
          }}
          datasetUrn={datasetUrn}
          rows={Array.from(selected.values())}
        />
      )}

      {/* pagination */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {showingFrom}–{showingTo} of {formatNumber(filtered)}
        </span>
        <div className="flex gap-1">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!canPrev || isFetching}
            onClick={() => setOffset(Math.max(0, offset - pageSize))}
          >
            Previous
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!canNext || isFetching}
            onClick={() => setOffset(offset + pageSize)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}

"use client";
import { useMemo } from "react";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { toLabel } from "@/lib/charts/geometry";

/** A row in the grid: the raw shaped cell array plus a stable id. */
interface GridRow {
  __id: string;
  cells: unknown[];
}

/** Build DataTable columns from the shaped column-name list (pass-through). */
export function gridColumns(columns: string[]): Column<GridRow>[] {
  return columns.map((name, ci) => ({
    id: `c${ci}`,
    header: name,
    cell: (row: GridRow) => <span className="font-mono">{toLabel(row.cells[ci])}</span>,
  }));
}

/** Wrap shaped rows (any[][]) into id-carrying grid rows (pass-through cells). */
export function gridRows(rows: unknown[][]): GridRow[] {
  return rows.map((r, ri) => ({ __id: String(ri), cells: Array.isArray(r) ? r : [r] }));
}

/**
 * Grid-family renderer: the shaped columns/rows go straight into the sanctioned
 * DataTable primitive (no bespoke <table>). Columns and rows are pass-through.
 */
export function GridChart({
  columns,
  rows,
  title,
  onSelect,
  selectedValue,
}: {
  columns: unknown;
  rows: unknown;
  title?: string;
  /** Cross-filter: clicking a row emits its first-column value (CHART-FR-041). */
  onSelect?: (value: string) => void;
  /** The currently-selected first-column value; that row is highlighted. */
  selectedValue?: string | null;
}) {
  const cols = useMemo(() => (Array.isArray(columns) ? columns.map(toLabel) : []), [columns]);
  const gRows = useMemo(() => gridRows(Array.isArray(rows) ? (rows as unknown[][]) : []), [rows]);
  const tableCols = useMemo(() => gridColumns(cols), [cols]);

  // Highlight the row whose first cell matches the active selection.
  const selectedIds = useMemo(() => {
    if (selectedValue == null) return undefined;
    const match = gRows.find((r) => toLabel(r.cells[0]) === String(selectedValue));
    return match ? new Set([match.__id]) : undefined;
  }, [gRows, selectedValue]);

  if (cols.length === 0 && gRows.length === 0) {
    return <p className="py-6 text-center text-xs text-muted-foreground">No data available.</p>;
  }

  return (
    <DataTable
      ariaLabel={title ?? "Chart data"}
      rows={gRows}
      columns={tableCols}
      rowId={(r) => r.__id}
      estimateRowHeight={36}
      onRowActivate={onSelect ? (r) => onSelect(toLabel(r.cells[0])) : undefined}
      selectedIds={selectedIds}
      emptyState={<p className="p-6 text-center text-xs text-muted-foreground">No rows.</p>}
    />
  );
}

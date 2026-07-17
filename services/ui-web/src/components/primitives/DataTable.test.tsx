import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataTable, type Column } from "./DataTable";

interface Row {
  id: string;
  name: string;
}

const rows: Row[] = Array.from({ length: 1000 }, (_, i) => ({ id: `r${i}`, name: `Row ${i}` }));
const columns: Column<Row>[] = [{ id: "name", header: "Name", cell: (r) => r.name }];

describe("DataTable virtualization (UI-FR-010, AC-2)", () => {
  it("renders far fewer DOM rows than the dataset over 1000 rows", () => {
    render(<DataTable ariaLabel="Rows" rows={rows} columns={columns} rowId={(r) => r.id} />);
    const bodyRows = document.querySelectorAll("[data-row-index]");
    // Windowed: never render all 1000 rows into the DOM (BR: no freeze > 500).
    expect(bodyRows.length).toBeLessThan(100);
  });

  it("exposes ARIA grid semantics with the full logical row count", () => {
    render(<DataTable ariaLabel="Rows" rows={rows} columns={columns} rowId={(r) => r.id} />);
    const grid = screen.getByRole("grid", { name: "Rows" });
    expect(grid).toHaveAttribute("aria-rowcount", "1000");
  });

  it("renders the empty state when there are no rows", () => {
    render(
      <DataTable
        ariaLabel="Rows"
        rows={[]}
        columns={columns}
        rowId={(r) => r.id}
        emptyState={<div>Nothing here</div>}
      />,
    );
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});

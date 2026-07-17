import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** graphqlRequest is routed by operation name; we capture the vars of every
 * DatasetRows call to assert the grid drives server-side paging/sort/filter. */
let latest: any = null;
const calls: { doc: string; vars: any }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => {
      calls.push({ doc, vars });
      if (doc.includes("query DatasetRows")) latest = vars;
      const total = 30;
      const hasFilter = (vars.filters ?? []).length > 0;
      const filtered = hasFilter ? 4 : total;
      return Promise.resolve({
        datasetRows: {
          columns: ["claim_id", "amount"],
          rows: Array.from({ length: Math.min(vars.limit, filtered) }, (_, i) => [
            `C${vars.offset + i}`,
            String((vars.offset + i) * 10),
          ]),
          total,
          filtered,
          offset: vars.offset,
          limit: vars.limit,
        },
      });
    },
  };
});

import { DatasetRowsGrid } from "./DatasetRowsGrid";

function lastRowsVars() {
  return latest;
}

beforeEach(() => {
  calls.length = 0;
  latest = null;
});

describe("DatasetRowsGrid", () => {
  it("shows the total row count and requests the default page", async () => {
    renderWithProviders(<DatasetRowsGrid datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByText("30")).toBeInTheDocument());
    expect(lastRowsVars()).toMatchObject({ offset: 0, limit: 50, filters: [] });
  });

  it("changing rows-per-page re-requests with the new limit and resets offset", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DatasetRowsGrid datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByText("30")).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Rows per page"), "100");
    await waitFor(() => expect(lastRowsVars()).toMatchObject({ limit: 100, offset: 0 }));
  });

  it("applying a column filter re-requests with a contains filter and shows the matched count", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DatasetRowsGrid datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByText("30")).toBeInTheDocument());
    await user.type(screen.getByLabelText("claim_id"), "C1");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() =>
      expect(lastRowsVars().filters).toEqual([{ col: "claim_id", op: "contains", value: "C1" }]),
    );
    // header flips to "4 of 30 rows match" (count text spans multiple nodes)
    await waitFor(() =>
      expect(
        screen.getAllByText((_, el) => el?.textContent === "4 of 30 rows match").length,
      ).toBeGreaterThan(0),
    );
  });

  it("clicking a column header cycles sort asc → desc → cleared", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DatasetRowsGrid datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByText("30")).toBeInTheDocument());
    const header = screen.getByRole("button", { name: /amount/i });
    await user.click(header);
    await waitFor(() => expect(lastRowsVars()).toMatchObject({ sort: "amount", dir: "asc" }));
    await user.click(header);
    await waitFor(() => expect(lastRowsVars()).toMatchObject({ sort: "amount", dir: "desc" }));
    await user.click(header);
    await waitFor(() => expect(lastRowsVars()).toMatchObject({ sort: null, dir: null }));
  });

  it("Next advances the offset by the page size (when more pages exist)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DatasetRowsGrid datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByText("30")).toBeInTheDocument());
    // 30 rows: page size 50 → single page (Next disabled). Drop to 25 → 2 pages.
    await user.selectOptions(screen.getByLabelText("Rows per page"), "25");
    await waitFor(() => expect(lastRowsVars()).toMatchObject({ limit: 25, offset: 0 }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastRowsVars()).toMatchObject({ offset: 25, limit: 25 }));
  });
});

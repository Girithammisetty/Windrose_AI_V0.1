import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

let aggVars: any = null;
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => {
      if (doc.includes("query DatasetRows")) {
        return Promise.resolve({
          datasetRows: { columns: ["product_type", "billed_amount"], rows: [["PPO", "100"]], total: 1, filtered: 1, offset: 0, limit: 1 },
        });
      }
      if (doc.includes("query DatasetAggregate")) {
        aggVars = vars;
        return Promise.resolve({
          datasetAggregate: {
            columns: [vars.dimension, vars.measure ? `${vars.agg}_${vars.measure}` : vars.agg],
            rows: [["PPO", "14"], ["HMO", "7"]],
            sql: "SELECT ...",
          },
        });
      }
      return Promise.resolve({});
    },
  };
});

import { DatasetQuickChart } from "./DatasetQuickChart";

beforeEach(() => {
  aggVars = null;
});

describe("DatasetQuickChart", () => {
  it("aggregates count by the first column by default", async () => {
    renderWithProviders(<DatasetQuickChart datasetId="ds-1" />);
    await waitFor(() =>
      expect(aggVars).toMatchObject({ dimension: "product_type", agg: "count" }),
    );
    // the generated-query disclosure proves the aggregate ran
    await waitFor(() => expect(screen.getByText("Show generated query")).toBeInTheDocument());
  });

  it("switching to sum requires a measure, then aggregates that column", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DatasetQuickChart datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByLabelText("Aggregate")).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Aggregate"), "sum");
    // before a measure is chosen, it prompts and does NOT run a sum
    expect(screen.getByText(/Choose a column to sum/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Of column/), "billed_amount");
    await waitFor(() =>
      expect(aggVars).toMatchObject({ agg: "sum", measure: "billed_amount", dimension: "product_type" }),
    );
  });

  it("lets the user change the group-by dimension", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DatasetQuickChart datasetId="ds-1" />);
    // wait until columns loaded + the default aggregate ran (options populated)
    await waitFor(() => expect(aggVars).toMatchObject({ dimension: "product_type" }));
    await user.selectOptions(screen.getByLabelText("Group by"), "billed_amount");
    await waitFor(() => expect(aggVars).toMatchObject({ dimension: "billed_amount" }));
  });
});

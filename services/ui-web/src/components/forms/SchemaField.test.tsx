import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import type { PipelineStepParam } from "@/lib/graphql/types";

/** Route graphqlRequest by operation to a per-test handler (repo convention). */
let handler: (doc: string, vars: any) => any = () => ({});
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => Promise.resolve(handler(doc, vars)),
  };
});

import { SchemaField } from "./SchemaField";

const datasetParam: PipelineStepParam = {
  name: "dataset",
  type: "dataset_ref",
  required: true,
  enumValues: null,
  min: null,
  max: null,
  help: null,
};

const datasetsPage = {
  datasets: {
    nodes: [
      { id: "ds-1", urn: "wr:t-acme:dataset:dataset/ds-1", name: "Claims 2026", tags: [] },
      { id: "ds-2", urn: "wr:t-acme:dataset:dataset/ds-2", name: "Policies", tags: [] },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const emptyPage = { datasets: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };

describe("SchemaField dataset_ref", () => {
  beforeEach(() => {
    handler = (doc: string) => (doc.includes("query Datasets") ? datasetsPage : {});
  });

  it("renders a dataset picker and emits the dataset URN on select", async () => {
    const onChange = vi.fn();
    renderWithProviders(<SchemaField param={datasetParam} value="" onChange={onChange} />);

    // Populated from the workspace's datasets (labels = names, values = URNs).
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Claims 2026" })).toBeInTheDocument(),
    );
    const select = screen.getByLabelText("dataset");
    expect(select.tagName).toBe("SELECT");

    await userEvent.selectOptions(select, "wr:t-acme:dataset:dataset/ds-2");
    expect(onChange).toHaveBeenCalledWith("wr:t-acme:dataset:dataset/ds-2");
  });

  it("preselects an existing URN value even before the list is inspected", async () => {
    renderWithProviders(
      <SchemaField param={datasetParam} value="wr:t-acme:dataset:dataset/ds-1" onChange={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Claims 2026" })).toBeInTheDocument(),
    );
    expect(screen.getByLabelText<HTMLSelectElement>("dataset").value).toBe(
      "wr:t-acme:dataset:dataset/ds-1",
    );
  });

  it("falls back to a free-text URN input when the workspace has no datasets", async () => {
    handler = (doc: string) => (doc.includes("query Datasets") ? emptyPage : {});
    const onChange = vi.fn();
    renderWithProviders(<SchemaField param={datasetParam} value="" onChange={onChange} />);

    // Loads to the empty state, swapping the picker for a free-text URN input.
    await waitFor(() => expect(screen.getByLabelText("dataset").tagName).toBe("INPUT"));
    const input = screen.getByLabelText("dataset");
    await userEvent.type(input, "wr:t-acme:dataset:dataset/manual");
    expect(onChange).toHaveBeenCalled();
  });
});

const param = (over: Partial<PipelineStepParam>): PipelineStepParam => ({
  name: "field",
  type: "string",
  required: false,
  enumValues: null,
  min: null,
  max: null,
  help: null,
  ...over,
});

describe("SchemaField data-aware formats", () => {
  const COLS = ["age", "income", "region"];

  it("renders a columns multi-select and emits a JSON array on select", async () => {
    const onChange = vi.fn();
    renderWithProviders(
      <SchemaField
        param={param({ name: "cols", type: "array", format: "columns" })}
        value=""
        availableColumns={COLS}
        onChange={onChange}
      />,
    );

    const select = screen.getByLabelText<HTMLSelectElement>("cols");
    expect(select.tagName).toBe("SELECT");
    expect(select.multiple).toBe(true);
    COLS.forEach((c) => expect(screen.getByRole("option", { name: c })).toBeInTheDocument());

    await userEvent.selectOptions(select, "income");
    const arg = onChange.mock.calls.at(-1)![0];
    expect(JSON.parse(arg as string)).toEqual(["income"]);
  });

  it("renders a column single-select over the available columns", async () => {
    const onChange = vi.fn();
    renderWithProviders(
      <SchemaField
        param={param({ name: "target", type: "string", format: "column" })}
        value=""
        availableColumns={COLS}
        onChange={onChange}
      />,
    );

    const select = screen.getByLabelText<HTMLSelectElement>("target");
    expect(select.tagName).toBe("SELECT");
    expect(select.multiple).toBe(false);

    await userEvent.selectOptions(select, "region");
    expect(onChange).toHaveBeenCalledWith("region");
  });

  it("renders an expression param as a monospace textarea", () => {
    renderWithProviders(
      <SchemaField
        param={param({ name: "expr", type: "string", format: "expression" })}
        value=""
        onChange={vi.fn()}
      />,
    );
    const ta = screen.getByLabelText("expr");
    expect(ta.tagName).toBe("TEXTAREA");
    expect(ta.className).toContain("font-mono");
  });

  it("degrades an UNKNOWN format to the base type widget (integer → number input)", () => {
    renderWithProviders(
      <SchemaField
        param={param({ name: "n", type: "integer", format: "wormhole" })}
        value=""
        availableColumns={COLS}
        onChange={vi.fn()}
      />,
    );
    const input = screen.getByLabelText<HTMLInputElement>("n");
    expect(input.tagName).toBe("INPUT");
    expect(input.type).toBe("number");
  });

  it("degrades columns → array textarea and column → free text when no columns resolve", () => {
    renderWithProviders(
      <SchemaField
        param={param({ name: "cols", type: "array", format: "columns" })}
        value=""
        availableColumns={[]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("cols").tagName).toBe("TEXTAREA");

    renderWithProviders(
      <SchemaField
        param={param({ name: "target", type: "string", format: "column" })}
        value=""
        availableColumns={[]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("target").tagName).toBe("INPUT");
  });
});

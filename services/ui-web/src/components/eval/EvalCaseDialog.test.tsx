import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let latest: any = null;
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    graphqlRequest: (doc: string, vars: any) => {
      if (doc.includes("mutation CreateEvalCase")) {
        latest = vars;
        return Promise.resolve({ createEvalCase: { id: "case-1", suiteId: "s", version: 1 } });
      }
      return Promise.resolve({});
    },
  };
});

import { EvalCaseDialog } from "./EvalCaseDialog";

beforeEach(() => {
  latest = null;
});

describe("EvalCaseDialog JSON validation", () => {
  it("blocks submit and surfaces a parse error when the input JSON is invalid", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <EvalCaseDialog open onOpenChange={() => {}} defaultDatasetKey="claims-agent/nl2sql" />,
    );

    fireEvent.change(screen.getByLabelText(/Input \(JSON\)/), { target: { value: "{ not json" } });
    fireEvent.change(screen.getByLabelText(/Expected \(JSON\)/), { target: { value: "{}" } });

    await user.click(screen.getByRole("button", { name: "Create case" }));

    // an error line is shown for the invalid field and the mutation never fires
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(latest).toBeNull();
  });

  it("submits the parsed JSON payload once both fields are valid", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <EvalCaseDialog open onOpenChange={() => {}} defaultDatasetKey="claims-agent/nl2sql" />,
    );

    fireEvent.change(screen.getByLabelText(/Input \(JSON\)/), { target: { value: '{"q":"hi"}' } });
    fireEvent.change(screen.getByLabelText(/Expected \(JSON\)/), {
      target: { value: '{"sql":"SELECT 1"}' },
    });

    await user.click(screen.getByRole("button", { name: "Create case" }));

    await waitFor(() => expect(latest).not.toBeNull());
    expect(latest.input).toMatchObject({
      datasetKey: "claims-agent/nl2sql",
      input: { q: "hi" },
      expected: { sql: "SELECT 1" },
    });
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import type { SemanticModelVersion } from "@/lib/graphql/types";

/** Route graphqlRequest by operation name to a per-test handler; keep the real
 * GraphQLRequestError (the component branches on `instanceof`). */
let handler: (doc: string, vars: any) => any = () => ({});
const requests: { doc: string; vars: any }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: async (doc: string, vars: any) => {
      requests.push({ doc, vars });
      return handler(doc, vars);
    },
  };
});

import { GraphQLRequestError } from "@/lib/graphql/client";
import { DefinitionEditor } from "./DefinitionEditor";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const DATASET_URN = "wr:t-42:dataset:dataset/d1";

const DRAFT_VERSION: SemanticModelVersion = {
  id: "ver-2",
  urn: "wr:t-42:semantic:version/ver-2",
  modelId: "sm-1",
  versionNo: 2,
  status: "DRAFT",
  definitionJson: {
    entities: [
      { name: "claims", dataset_urn: DATASET_URN, table: "main.claims", primary_key: ["claim_id"],
        dataset_version_policy: { policy: "latest" } },
    ],
    dimensions: [
      { name: "claim_type", entity: "claims", column: "claim_type", type: "categorical", time_grains: [], synonyms: [], deprecated: false },
    ],
    measures: [
      { name: "claim_count", entity: "claims", agg: "count", synonyms: [], deprecated: false },
    ],
    join_paths: [],
  },
};

const datasetsResult = { datasets: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string, vars: any) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Datasets")) return datasetsResult;
    if (doc.includes("query DatasetSchema")) {
      return {
        datasetSchema: [
          { name: "claim_type", type: "string", nullable: false, tags: [], inferred: false },
          { name: "amount", type: "double", nullable: true, tags: [], inferred: false },
        ],
      };
    }
    if (doc.includes("mutation UpdateSemanticModelDraft")) {
      return { updateSemanticModelDraft: { ...DRAFT_VERSION, definitionJson: vars.definition } };
    }
    return {};
  };
});

describe("DefinitionEditor — entity/dimension/measure authoring bound to real dataset columns", () => {
  it("offers the real dataset's columns (from datasetSchema) in the dimension column picker", async () => {
    renderWithProviders(<DefinitionEditor modelId="sm-1" version={DRAFT_VERSION} onSubmitted={() => {}} />);
    await screen.findByDisplayValue("claim_type"); // the dimension name input
    const columnSelect = await screen.findByLabelText("Column", { selector: "select" });
    await waitFor(() => {
      expect(within(columnSelect).getByText("amount (double)")).toBeInTheDocument();
    });
  });

  it("debounces autosave: editing a field posts updateSemanticModelDraft with the edited definition", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DefinitionEditor modelId="sm-1" version={DRAFT_VERSION} onSubmitted={() => {}} />);
    const measureName = await screen.findByDisplayValue("claim_count");
    await user.clear(measureName);
    await user.type(measureName, "total_claims");

    await waitFor(
      () => {
        const call = requests.find((r) => r.doc.includes("mutation UpdateSemanticModelDraft"));
        expect(call?.vars.definition.measures[0].name).toBe("total_claims");
      },
      { timeout: 3000 },
    );
    expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
  });

  it("a save-time structural 422 is surfaced without discarding the author's edits", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Datasets")) return datasetsResult;
      if (doc.includes("query DatasetSchema")) return { datasetSchema: [] };
      if (doc.includes("mutation UpdateSemanticModelDraft")) {
        throw new GraphQLRequestError(
          [{ message: "illegal column identifier 'SELECT'", extensions: { code: "VALIDATION_FAILED" } }],
          422,
        );
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<DefinitionEditor modelId="sm-1" version={DRAFT_VERSION} onSubmitted={() => {}} />);
    const measureName = await screen.findByDisplayValue("claim_count");
    await user.type(measureName, "_x");

    expect(await screen.findByTestId("save-error", {}, { timeout: 3000 })).toHaveTextContent(
      "illegal column identifier",
    );
    // The author's in-progress edit is still visible (not reverted).
    expect(screen.getByDisplayValue("claim_count_x")).toBeInTheDocument();
  });

  it("submit surfaces the full [{object,problem}] list mapped onto the offending row, and stays a draft", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Datasets")) return datasetsResult;
      if (doc.includes("query DatasetSchema")) return { datasetSchema: [] };
      if (doc.includes("mutation SubmitSemanticModelVersion")) {
        throw new GraphQLRequestError(
          [
            {
              message: "definition validation failed",
              extensions: {
                code: "VALIDATION_FAILED",
                details: [{ object: "dimension/claim_type", problem: "column 'claim_type' not in dataset schema of entity 'claims'" }],
              },
            },
          ],
          422,
        );
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<DefinitionEditor modelId="sm-1" version={DRAFT_VERSION} onSubmitted={() => {}} />);
    await screen.findByDisplayValue("claim_type");
    const submitButton = await screen.findByRole("button", { name: "Submit for review" });
    await user.click(submitButton);

    // Surfaced in TWO places: inline on the offending dimension row, and in the
    // top-level issues summary — both real, not a test artifact.
    const matches = await screen.findAllByText(/column 'claim_type' not in dataset schema/);
    expect(matches.length).toBe(2);
    const dimensionRow = screen.getByTestId("dimension-row-0");
    expect(within(dimensionRow).getByText(/column 'claim_type' not in dataset schema/)).toBeInTheDocument();
    expect(screen.getByText("1 issue(s)")).toBeInTheDocument();
    // Still shows the submit button — a failed submit does not lock the editor.
    expect(await screen.findByRole("button", { name: "Submit for review" })).toBeEnabled();
  });
});

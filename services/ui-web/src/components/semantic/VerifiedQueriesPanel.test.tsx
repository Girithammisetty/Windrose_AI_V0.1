import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** Route graphqlRequest by operation name to a per-test handler. */
let handler: (doc: string, vars: any) => any = () => ({});
const requests: { doc: string; vars: any }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => {
      requests.push({ doc, vars });
      return Promise.resolve(handler(doc, vars));
    },
  };
});

import { VerifiedQueriesPanel } from "./VerifiedQueriesPanel";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

function vq(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "vq-1", urn: "wr:t:semantic:verified_query/vq-1", workspaceId: "ws", modelId: null,
    nlText: "top claims?", sqlText: "SELECT 1", variables: [], status: "DRAFT", tags: [],
    provenance: null, healthNote: null, submittedBy: "author-1", approvedBy: null, decidedAt: null,
    createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query VerifiedQueries")) {
      return { verifiedQueries: { nodes: [vq()], pageInfo: { nextCursor: null, hasMore: false } } };
    }
    if (doc.includes("mutation CreateVerifiedQuery")) return { createVerifiedQuery: vq({ id: "vq-new" }) };
    if (doc.includes("query VerifiedQuerySearch")) {
      return {
        verifiedQuerySearch: [
          { id: "vq-1", nlText: "top open claims by reserve", sqlText: "SELECT reserve FROM claims",
            variables: [], tags: ["hero"], modelId: null, score: 0.912 },
        ],
      };
    }
    return {};
  };
});

describe("VerifiedQueriesPanel (semantic-service four-eyes NL↔SQL governance)", () => {
  // DataTable rows never materialize in jsdom (virtualized) — assert on request
  // variables + aria-rowcount per the repo convention.
  it("lists verified pairs scoped to the viewer's workspace", async () => {
    renderWithProviders(<VerifiedQueriesPanel />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Verified queries" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    const call = requests.find((r) => r.doc.includes("query VerifiedQueries"));
    expect(call?.vars?.workspaceId).toBe("ws");
  });

  it("threads the status filter into the real variables (lowercase, service exact-match)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<VerifiedQueriesPanel />);
    await screen.findByRole("grid", { name: "Verified queries" });

    await user.selectOptions(screen.getByLabelText("Filter by verified-query status"), "pending_review");
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("query VerifiedQueries")).at(-1);
      expect(call?.vars?.status).toBe("pending_review");
    });
  });

  it("searches approved pairs: debounced query threads workspace + topK and renders the hits", async () => {
    const user = userEvent.setup();
    renderWithProviders(<VerifiedQueriesPanel />);
    await screen.findByRole("grid", { name: "Verified queries" });

    await user.type(screen.getByLabelText("Search approved pairs"), "reserve");

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("query VerifiedQuerySearch"));
      expect(call?.vars).toMatchObject({ query: "reserve", workspaceId: "ws", topK: 5 });
    });
    // the ANN hit's NL question + SQL + score render in the results list
    expect(await screen.findByText("top open claims by reserve")).toBeInTheDocument();
    expect(screen.getByText("SELECT reserve FROM claims")).toBeInTheDocument();
    expect(screen.getByText(/score 0\.912/)).toBeInTheDocument();
  });

  it("drafts a new pair with the real create variables + idempotency key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<VerifiedQueriesPanel />);

    await user.click(await screen.findByRole("button", { name: /new verified query/i }));
    await user.type(screen.getByLabelText("Question (natural language)"), "top claims by reserve?");
    await user.type(screen.getByLabelText("SQL"), "SELECT 1");
    await user.click(screen.getByRole("button", { name: /create draft/i }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateVerifiedQuery"));
      expect(call?.vars?.input).toMatchObject({ nlText: "top claims by reserve?", sqlText: "SELECT 1" });
      expect(call?.vars?.idempotencyKey).toBeTruthy();
    });
  });
});

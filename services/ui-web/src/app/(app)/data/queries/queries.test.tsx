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

import QueriesPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const savedQueriesPage = {
  savedQueries: {
    nodes: [
      { id: "q-1", urn: "wr:t:query:query/q-1", name: "Open claims by type", description: null, tags: [], moduleNames: ["insights"], versionNo: 2, createdAt: null, updatedAt: "2026-07-01T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const runResult = {
  runSql: {
    executionId: "ex-1",
    status: "succeeded",
    engine: "duckdb",
    cacheHit: false,
    durationMs: 9,
    resultRows: 1,
    scanBytes: 0,
    columns: [{ name: "example", type: "BIGINT" }],
    rows: [[1]],
    hasMore: false,
    warnings: null,
    error: null,
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("mutation CreateSavedQuery")) {
      return { createSavedQuery: { id: "q-new", urn: "wr:t:query:query/q-new", name: "saved", description: null,
        tags: [], moduleNames: ["claims"], sqlText: "SELECT 1 AS example", variables: [], versionNo: 1,
        createdAt: null, updatedAt: null } };
    }
    if (doc.includes("mutation DeleteSavedQuery")) return { deleteSavedQuery: true };
    if (doc.includes("query QueryExecutions")) {
      return { queryExecutions: { nodes: [
        { id: "e-1", urn: "wr:t:query:execution/e-1", status: "succeeded", engine: "duckdb", cacheHit: false,
          savedQueryId: "q-1", queryVersionNo: 2, createdBy: "u", createdAt: "2026-07-12T01:00:00Z",
          startedAt: "2026-07-12T01:00:01Z", finishedAt: "2026-07-12T01:00:02Z", durationMs: 42,
          resultRows: 10, scanBytes: 1024, queuePosition: null, error: null },
        { id: "e-2", urn: "wr:t:query:execution/e-2", status: "running", engine: "duckdb", cacheHit: false,
          savedQueryId: null, queryVersionNo: null, createdBy: "u", createdAt: "2026-07-12T02:00:00Z",
          startedAt: "2026-07-12T02:00:01Z", finishedAt: null, durationMs: null, resultRows: null,
          scanBytes: null, queuePosition: null, error: null },
      ], pageInfo: { nextCursor: null, hasMore: false } } };
    }
    if (doc.includes("savedQueries")) return savedQueriesPage;
    if (doc.includes("runSql")) return runResult;
    return {};
  };
});

describe("Queries page", () => {
  it("lists real saved queries from the query-service", async () => {
    renderWithProviders(<QueriesPage />);
    expect(await screen.findByText("Open claims by type")).toBeInTheDocument();
  });

  it("runs ad-hoc SQL and renders the returned columns + rows", async () => {
    const user = userEvent.setup();
    renderWithProviders(<QueriesPage />);

    // The primary Run button executes the ad-hoc editor content.
    const runButtons = await screen.findAllByRole("button", { name: /^run/i });
    await user.click(runButtons[0]);

    await waitFor(() => expect(screen.getByText("example")).toBeInTheDocument());
    // The single result cell renders the returned value.
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText(/duckdb/i)).toBeInTheDocument();
  });

  it("saves the editor's SQL as a new governed query with real create variables", async () => {
    const user = userEvent.setup();
    renderWithProviders(<QueriesPage />);

    await user.click(await screen.findByRole("button", { name: /save query/i }));
    await user.type(screen.getByLabelText("Name"), "top_claims");
    await user.type(screen.getByLabelText("Modules"), "claims, fraud");
    await user.click(screen.getByRole("button", { name: /^save query$/i, hidden: false }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateSavedQuery"));
      expect(call?.vars?.input).toMatchObject({
        name: "top_claims",
        moduleNames: ["claims", "fraud"],
        sqlText: "SELECT 1 AS example",
        variables: [],
      });
      expect(call?.vars?.idempotencyKey).toBeTruthy();
    });
    // Real success notice, driven by the mutation result's version.
    expect(await screen.findByTestId("notice-banner")).toHaveTextContent("v1");
  });

  it("deletes a saved query behind the ConfirmDialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<QueriesPage />);

    await user.click(await screen.findByRole("button", { name: "Delete Open claims by type" }));
    // Confirm inside the destructive dialog.
    const dialogButtons = await screen.findAllByRole("button", { name: /^delete$/i });
    await user.click(dialogButtons[dialogButtons.length - 1]);

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation DeleteSavedQuery"));
      expect(call?.vars).toEqual({ id: "q-1" });
    });
  });

  it("shows the execution history tab with real queryExecutions data (aria-rowcount; rows are virtualized)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<QueriesPage />);

    await user.click(await screen.findByRole("tab", { name: /executions/i }));
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Execution history" });
      expect(grid).toHaveAttribute("aria-rowcount", "2");
    });
    expect(requests.some((r) => r.doc.includes("query QueryExecutions"))).toBe(true);

    // Threads the status filter into the real query variables (lowercase —
    // query-service filters exact-match over lowercase stored values).
    await user.selectOptions(screen.getByLabelText("Filter by execution status"), "failed");
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("query QueryExecutions")).at(-1);
      expect(call?.vars?.status).toBe("failed");
    });
  });
});

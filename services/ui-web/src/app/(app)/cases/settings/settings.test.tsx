import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import CaseSettingsPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};
const dispositionsResult = {
  dispositions: [
    { id: "d-1", urn: "wr:t:case:disposition/d-1", workspaceId: "ws", code: "fraud_confirmed",
      label: "Fraud confirmed", category: "true_positive", requiresNote: true, active: true,
      createdAt: null, updatedAt: null },
    { id: "d-2", urn: "wr:t:case:disposition/d-2", workspaceId: "ws", code: "benign_dup",
      label: "Benign duplicate", category: "benign", requiresNote: false, active: true,
      createdAt: null, updatedAt: null },
    { id: "d-3", urn: "wr:t:case:disposition/d-3", workspaceId: "ws", code: "retired",
      label: "Retired", category: "other", requiresNote: false, active: false,
      createdAt: null, updatedAt: null },
  ],
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Dispositions")) return dispositionsResult;
    if (doc.includes("query CaseFields")) return { caseFields: [] };
    if (doc.includes("query Users")) {
      return { users: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
    }
    return {};
  };
});

describe("Case settings — dispositions catalog", () => {
  // NOTE: DataTable is windowed (useVirtualizer) and jsdom has no layout, so
  // row CONTENT never materializes in tests — the repo convention is to assert
  // request variables and the grid's logical aria-rowcount instead of row text.
  it("renders the real workspace catalog in the grid (aria-rowcount)", async () => {
    renderWithProviders(<CaseSettingsPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Dispositions" });
      expect(grid).toHaveAttribute("aria-rowcount", "3");
    });
    expect(requests.some((r) => r.doc.includes("query Dispositions"))).toBe(true);
  });

  it("createDisposition sends the composed code/label/category/requiresNote", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Dispositions")) return dispositionsResult;
      if (doc.includes("query CaseFields")) return { caseFields: [] };
      if (doc.includes("mutation CreateDisposition")) {
        return { createDisposition: { id: "d-new", urn: "wr:t:case:disposition/d-new",
          workspaceId: "ws", ...vars.input, createdAt: null, updatedAt: null } };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<CaseSettingsPage />);

    await user.click(await screen.findByRole("button", { name: "New disposition" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Code"), "staged_accident");
    await user.type(within(dialog).getByLabelText("Label"), "Staged accident");
    await user.selectOptions(within(dialog).getByLabelText("Category"), "false_positive");
    await user.click(within(dialog).getByLabelText(/require a resolution note/i));
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateDisposition"));
      expect(call?.vars?.input).toEqual({
        code: "staged_accident",
        label: "Staged accident",
        category: "false_positive",
        requiresNote: true,
      });
      expect(call?.vars?.idempotencyKey).toBeTruthy();
    });
  });
});

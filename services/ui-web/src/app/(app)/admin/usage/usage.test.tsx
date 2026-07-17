import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Same conventions as admin/teams/teams.test.tsx: graphqlRequest is routed by
 * operation name, the viewer is a full admin, and selection is driven through
 * the create-flow rather than a DataTable row click (DataTable virtualizes
 * against the real scroll container height, which jsdom always reports as 0 —
 * see teams.test.tsx's header comment and DataTable.test.tsx for the same
 * repo-wide constraint).
 */
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

import AdminUsagePage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const emptyBudgets = { budgets: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
const emptyRateCards = { rateCards: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
const emptyCostPanel = { workspaceCostPanel: { rows: [], budgetStates: [] } };
const emptyAnomalies = { anomalies: [] };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query WorkspaceCostPanel")) return emptyCostPanel;
    if (doc.includes("query Anomalies")) return emptyAnomalies;
    if (doc.includes("query Budgets")) return emptyBudgets;
    if (doc.includes("query RateCards")) return emptyRateCards;
    if (doc.includes("mutation CreateBudget")) {
      return {
        createBudget: {
          id: "b-new", urn: "wr:t:usage:budget/b-new", scope: "workspace/ws-9", meterKey: "tokens",
          window: "calendar_month", limitUsd: 250, thresholds: [80, 95, 100], actionAt100: "hard_stop",
          status: "active", createdAt: null, updatedAt: null,
        },
      };
    }
    if (doc.includes("mutation UpdateBudget")) {
      return {
        updateBudget: {
          id: "b-new", urn: "wr:t:usage:budget/b-new", scope: "workspace/ws-9", meterKey: "tokens",
          window: "calendar_month", limitUsd: 500, thresholds: [80, 95, 100], actionAt100: "hard_stop",
          status: "active", createdAt: null, updatedAt: null,
        },
      };
    }
    if (doc.includes("mutation DeleteBudget")) return { deleteBudget: true };
    if (doc.includes("mutation CreateRateCard")) {
      return {
        createRateCard: {
          id: "rc-new", urn: "wr:t:usage:ratecard/rc-new", version: 2, effectiveFrom: "2026-08-01",
          status: "draft", items: { api_calls: 0.002 }, createdAt: null,
        },
      };
    }
    if (doc.includes("mutation ActivateRateCard")) {
      return {
        activateRateCard: {
          id: "rc-new", urn: "wr:t:usage:ratecard/rc-new", version: 2, effectiveFrom: "2026-08-01",
          status: "active", items: { api_calls: 0.002 }, createdAt: null,
        },
      };
    }
    return {};
  };
});

describe("Admin Usage page — budget administration", () => {
  it("creates a budget via createBudget and selects it for editing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminUsagePage />);

    await user.click(await screen.findByRole("button", { name: "New budget" }));
    await user.clear(screen.getByLabelText("Meter key"));
    await user.type(screen.getByLabelText("Meter key"), "tokens");
    await user.clear(screen.getByLabelText("Limit USD"));
    await user.type(screen.getByLabelText("Limit USD"), "250");
    await user.selectOptions(screen.getByLabelText("At 100%"), "hard_stop");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateBudget"));
      expect(call?.vars.input).toMatchObject({ meterKey: "tokens", limitUsd: 250, actionAt100: "hard_stop" });
    });
    expect(await screen.findByRole("heading", { name: "tokens" })).toBeInTheDocument();
  });

  it("updates the selected budget's limit via updateBudget", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminUsagePage />);
    await user.click(await screen.findByRole("button", { name: "New budget" }));
    await user.clear(screen.getByLabelText("Meter key"));
    await user.type(screen.getByLabelText("Meter key"), "tokens");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByRole("heading", { name: "tokens" });

    await user.clear(screen.getByLabelText("Edit limit USD"));
    await user.type(screen.getByLabelText("Edit limit USD"), "500");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation UpdateBudget"));
      expect(call?.vars).toMatchObject({ id: "b-new", input: { limitUsd: 500 } });
    });
  });

  it("deletes the selected budget through the confirm dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminUsagePage />);
    await user.click(await screen.findByRole("button", { name: "New budget" }));
    await user.clear(screen.getByLabelText("Meter key"));
    await user.type(screen.getByLabelText("Meter key"), "tokens");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByRole("heading", { name: "tokens" });

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation DeleteBudget") && r.vars.id === "b-new")).toBe(true);
    });
  });
});

describe("Admin Usage page — rate cards", () => {
  it("creates a draft rate card and activates it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminUsagePage />);

    await user.click(await screen.findByRole("button", { name: "New rate card" }));
    await user.clear(screen.getByLabelText("Rate card version"));
    await user.type(screen.getByLabelText("Rate card version"), "2");
    await user.clear(screen.getByLabelText("Rate card items"));
    await user.type(screen.getByLabelText("Rate card items"), "api_calls=0.002");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateRateCard"));
      expect(call?.vars.input).toMatchObject({ version: 2, items: { api_calls: 0.002 } });
    });

    const activate = await screen.findByRole("button", { name: "Activate" });
    await user.click(activate);

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation ActivateRateCard"));
      expect(call?.vars).toMatchObject({ id: "rc-new" });
    });
    expect(await screen.findByRole("heading", { name: "v2 · active" })).toBeInTheDocument();
  });
});

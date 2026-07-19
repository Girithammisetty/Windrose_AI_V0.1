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

import ModelArchetypesPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};
const archetypesResult = {
  modelArchetypes: [
    {
      id: "a-1", archetypeKey: "vendor_fraud_risk_score", workspaceId: "ws-1",
      name: "Vendor fraud-risk score", taskType: "binary_classification",
      target: "vendor_fraud_escalation", description: "Scores a vendor for fraud indicators.",
      expectedMetrics: { precision_min: 0.9 }, governanceNotes: "HOLD-only; four-eyes on every block.",
      createdAt: null,
    },
  ],
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query ModelArchetypes")) return archetypesResult;
    if (doc.includes("mutation CreateModelArchetype")) {
      return { createModelArchetype: { id: "a-2", archetypeKey: "x", name: "X" } };
    }
    return {};
  };
});

describe("Model archetypes registry editor (inc16)", () => {
  it("lists archetypes with task type, target and governance", async () => {
    renderWithProviders(<ModelArchetypesPage />);
    expect(await screen.findByText("Vendor fraud-risk score")).toBeInTheDocument();
    expect(screen.getByText("vendor_fraud_risk_score")).toBeInTheDocument();
    expect(screen.getByText("binary_classification")).toBeInTheDocument();
    expect(screen.getByText("vendor_fraud_escalation")).toBeInTheDocument();
    expect(screen.getByText(/HOLD-only/)).toBeInTheDocument();
    expect(screen.getByText(/precision_min/)).toBeInTheDocument();
  });

  it("creates a new archetype through the governed mutation", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ModelArchetypesPage />);
    await screen.findByText("Vendor fraud-risk score");

    await user.click(screen.getByRole("button", { name: /new archetype/i }));
    await user.type(screen.getByLabelText("Key"), "duplicate_pair_confidence");
    await user.type(screen.getByLabelText("Name"), "Duplicate pair confidence");
    await user.click(screen.getByRole("button", { name: /add archetype/i }));

    await waitFor(() => {
      const create = requests.find((r) => r.doc.includes("mutation CreateModelArchetype"));
      expect(create).toBeTruthy();
      expect(create!.vars.input.archetypeKey).toBe("duplicate_pair_confidence");
      expect(create!.vars.input.name).toBe("Duplicate pair confidence");
      expect(create!.vars.input.taskType).toBe("binary_classification");
    });
  });
});

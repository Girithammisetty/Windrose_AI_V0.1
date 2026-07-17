import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** graphqlRequest routed by operation name; viewer is a full admin. DataTable
 * rows never materialize in jsdom (virtualized) — assertions target request
 * variables + aria-rowcount (repo convention). */
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

import AdminToolsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const toolsPage = {
  tools: {
    nodes: [
      { toolId: "case.assign", displayName: "Assign case", ownerService: "case-service", ownerTeam: "claims",
        enabledByDefault: true, sideEffects: "reversible", tags: ["case"], createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z" },
      { toolId: "pipeline.launch_run", displayName: "Launch pipeline run", ownerService: "pipeline-orchestrator", ownerTeam: "ml",
        enabledByDefault: false, sideEffects: "reversible", tags: [], createdAt: "2026-07-11T00:00:00Z", updatedAt: "2026-07-11T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const byoPending = {
  byoSubmissions: [
    { id: "byo-1", manifest: {}, endpointUrl: "https://ext.example.com/mcp", authMethod: "api_key",
      requestedTier: "read", egressDescription: "", status: "pending_approval", decidedBy: null,
      decisionMessage: null, createdAt: "2026-07-12T00:00:00Z" },
  ],
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Tools(")) return toolsPage;
    if (doc.includes("query ByoSubmissions")) return byoPending;
    if (doc.includes("mutation RegisterTool")) return { registerTool: toolsPage.tools.nodes[0] };
    if (doc.includes("mutation SubmitByoTool")) return { submitByoTool: byoPending.byoSubmissions[0] };
    return {};
  };
});

describe("Admin tool-registry page", () => {
  it("lists the catalog and the BYO queue from the real queries", async () => {
    renderWithProviders(<AdminToolsPage />);
    await waitFor(() => {
      expect(screen.getByRole("grid", { name: "Catalog" })).toHaveAttribute("aria-rowcount", "2");
    });
    await waitFor(() => {
      expect(screen.getByRole("grid", { name: "BYO onboarding queue" })).toHaveAttribute("aria-rowcount", "1");
    });
    // The queue defaults to the pending filter (the approver's work list).
    const byoCall = requests.find((r) => r.doc.includes("query ByoSubmissions"));
    expect(byoCall?.vars?.status).toBe("pending_approval");
  });

  it("registers a tool with the entered manifest fields", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminToolsPage />);
    await screen.findByRole("grid", { name: "Catalog" });

    await user.click(screen.getByRole("button", { name: "Register tool" }));
    await user.type(screen.getByLabelText("Tool id"), "case.assign");
    await user.type(screen.getByLabelText("Owner service"), "case-service");
    await user.selectOptions(screen.getByLabelText("Side effects"), "reversible");
    await user.click(screen.getByRole("button", { name: "Register tool" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RegisterTool"));
      expect(call?.vars?.input).toMatchObject({
        toolId: "case.assign",
        ownerService: "case-service",
        sideEffects: "reversible",
      });
    });
  });

  it("threads the BYO status filter into the query variables", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminToolsPage />);
    await screen.findByRole("grid", { name: "BYO onboarding queue" });

    await user.selectOptions(screen.getByLabelText("Filter BYO submissions by status"), "approved");
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("query ByoSubmissions")).at(-1);
      expect(call?.vars?.status).toBe("approved");
    });
  });

  it("submits an external (BYO) tool capped at write-proposal", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminToolsPage />);
    await screen.findByRole("grid", { name: "BYO onboarding queue" });

    await user.click(screen.getByRole("button", { name: "Submit external tool" }));
    await user.type(screen.getByLabelText("BYO endpoint URL"), "https://ext.example.com/mcp");
    await user.selectOptions(screen.getByLabelText("Requested tier"), "write-proposal");
    await user.click(screen.getByRole("button", { name: "Submit external tool" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation SubmitByoTool"));
      expect(call?.vars?.input).toMatchObject({
        endpointUrl: "https://ext.example.com/mcp",
        requestedTier: "write-proposal",
      });
    });
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Note: the list renders through DataTable, which windows its rows via
 * @tanstack/react-virtual measuring the real scroll container height — jsdom
 * always reports 0, so no row text ever mounts in this environment (see
 * DataTable.test.tsx + admin/teams/teams.test.tsx for the same convention).
 * These tests therefore verify the list request's variables and the
 * navigation/empty-state paths (which don't depend on virtualized rows)
 * rather than row text content.
 */
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

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import SemanticModelsPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

function model(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "sm-1", urn: "wr:t-42:semantic:model/sm-1", workspaceId: "ws", name: "claims_core",
    description: "Claims semantic model", publishedVersionNo: 2, draftVersionNo: null,
    healthStatus: "ok", createdBy: "u-1", createdAt: "2026-07-10T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  push.mockClear();
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query SemanticModelList")) {
      return { semanticModelList: { nodes: [model()], pageInfo: { nextCursor: null, hasMore: false } } };
    }
    return {};
  };
});

describe("SemanticModelsPage (semantic-service model authoring list)", () => {
  it("queries semanticModelList scoped to the viewer's real workspace", async () => {
    renderWithProviders(<SemanticModelsPage />);
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("query SemanticModelList"));
      expect(call?.vars.workspaceId).toBe("ws");
    });
    // Grid mounted with the real logical row count (virtualized rows don't
    // render text in jsdom — see file header note).
    expect(await screen.findByRole("grid", { name: "Semantic Models" })).toHaveAttribute("aria-rowcount", "1");
  });

  it("navigates to the new-model page", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SemanticModelsPage />);
    await screen.findByRole("grid", { name: "Semantic Models" });
    await user.click(screen.getByRole("button", { name: /New model/i }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/data/semantic-models/new"));
  });

  it("shows the real empty state (with a create CTA) when no models exist yet", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query SemanticModelList")) {
        return { semanticModelList: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
      }
      return {};
    };
    renderWithProviders(<SemanticModelsPage />);
    expect(await screen.findByText("No semantic models yet")).toBeInTheDocument();
    // The empty-state CTA is a real, gated "create" affordance, not dead text.
    expect(screen.getAllByRole("button", { name: /New model/i }).length).toBeGreaterThan(0);
  });
});

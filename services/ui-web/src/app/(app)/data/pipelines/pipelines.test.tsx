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

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import PipelinesPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const templatesPage = {
  pipelineTemplates: {
    nodes: [
      { id: "tpl-1", urn: "wr:t:pipeline:template/tpl-1", name: "Claims training", pipelineType: "training",
        activeVersionId: "tv-2", definition: null, validationStatus: "valid", isSystem: false, archived: false,
        createdBy: null, createdAt: "2026-07-01T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  push.mockClear();
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query PipelineTemplates")) return templatesPage;
    return {};
  };
});

describe("Pipelines page (template lifecycle, Tier 4a)", () => {
  // DataTable rows never materialize in jsdom — assert on request variables +
  // aria-rowcount per the repo convention (runs.test.tsx).
  it("lists templates and threads includeArchived into the real query variables", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PipelinesPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Pipelines" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    // Default list omits archived templates.
    expect(requests.find((r) => r.doc.includes("query PipelineTemplates"))?.vars?.includeArchived).toBeUndefined();

    await user.click(screen.getByLabelText("Show archived"));
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("query PipelineTemplates")).at(-1);
      expect(call?.vars?.includeArchived).toBe(true);
    });
  });
});

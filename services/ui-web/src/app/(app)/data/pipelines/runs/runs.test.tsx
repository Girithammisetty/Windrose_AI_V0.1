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

import PipelineRunsPage from "./page";

const runsPage = {
  pipelineRuns: {
    nodes: [
      { id: "run-1", urn: "wr:t:pipeline:run/run-1", templateId: "tpl-1", status: "SUCCEEDED",
        createdAt: "2026-07-12T10:00:00Z", startedAt: "2026-07-12T10:00:05Z", finishedAt: "2026-07-12T10:02:00Z" },
      { id: "run-2", urn: "wr:t:pipeline:run/run-2", templateId: "tpl-unknown", status: "RUNNING",
        createdAt: "2026-07-12T11:00:00Z", startedAt: "2026-07-12T11:00:03Z", finishedAt: null },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const templatesPage = {
  pipelineTemplates: {
    nodes: [
      { id: "tpl-1", urn: "wr:t:pipeline:template/tpl-1", name: "Claims triage training",
        pipelineType: "training", validationStatus: "VALID", createdAt: "2026-07-01T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("pipelineRuns")) return runsPage;
    if (doc.includes("pipelineTemplates")) return templatesPage;
    return {};
  };
});

describe("Pipeline runs page (pipeline-orchestrator run history)", () => {
  // NOTE: DataTable is windowed (useVirtualizer) and jsdom has no layout, so
  // row CONTENT never materializes in tests — the repo convention (see
  // teams.test.tsx, DataTable.test.tsx) is to assert on request variables and
  // the grid's logical aria-rowcount instead of row text.
  it("fetches real runs + templates and reflects the full run count in the grid", async () => {
    renderWithProviders(<PipelineRunsPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Pipeline runs" });
      expect(grid).toHaveAttribute("aria-rowcount", "2");
    });
    // Both real queries fired: the run history and the template-name hydration.
    expect(requests.some((r) => r.doc.includes("pipelineRuns"))).toBe(true);
    expect(requests.some((r) => r.doc.includes("pipelineTemplates"))).toBe(true);
  });

  it("threads the status filter into the real pipelineRuns query variables", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PipelineRunsPage />);
    await screen.findByRole("grid", { name: "Pipeline runs" });

    // LOWERCASE is load-bearing: pipeline-orchestrator's status filter is
    // case-sensitive over lowercase stored values (live-verified).
    await user.selectOptions(screen.getByLabelText("Filter by run status"), "failed");
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("pipelineRuns")).at(-1);
      expect(call?.vars?.status).toBe("failed");
    });
  });
});

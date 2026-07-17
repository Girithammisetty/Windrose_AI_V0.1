import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** graphqlRequest routed by operation name. DataTable rows never materialize
 * in jsdom (virtualized) — assertions target request variables + aria-rowcount
 * (repo convention, see data/pipelines/runs/runs.test.tsx). */
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

import AgentRunsIndex from "./page";

const runs = {
  agentRuns: {
    nodes: [
      { id: "run-1", urn: "wr:t:agent:run/run-1", sessionId: "sess-1", agentKey: "case-triage", agentVersion: 1,
        status: "SUCCEEDED", principalType: "user_obo", usage: { input_tokens: 10, output_tokens: 20 },
        createdAt: "2026-07-12T01:00:00Z" },
      { id: "run-2", urn: "wr:t:agent:run/run-2", sessionId: "sess-2", agentKey: "analytics", agentVersion: 1,
        status: "RUNNING", principalType: "user_obo", usage: null, createdAt: "2026-07-12T02:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  push.mockClear();
  handler = (doc: string) => {
    if (doc.includes("query AgentRuns")) return runs;
    return {};
  };
});

describe("Agent run history page (agent-runtime GET /runs)", () => {
  it("lists the tenant's real run history in the grid", async () => {
    renderWithProviders(<AgentRunsIndex />);
    await waitFor(() => {
      expect(screen.getByRole("grid", { name: "Agent runs" })).toHaveAttribute("aria-rowcount", "2");
    });
    expect(requests.some((r) => r.doc.includes("query AgentRuns"))).toBe(true);
  });

  it("threads the agent-key filter into the agentRuns query variables", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentRunsIndex />);
    await screen.findByRole("grid", { name: "Agent runs" });

    await user.type(screen.getByLabelText("Filter runs by agent key"), "case-triage");
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("query AgentRuns")).at(-1);
      expect(call?.vars?.agentKey).toBe("case-triage");
    });
  });

  it("keeps the open-by-id deep-link box working (URN → run id)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentRunsIndex />);
    await screen.findByRole("grid", { name: "Agent runs" });

    await user.type(screen.getByLabelText("Open a run by id or URN"), "wr:t-1:agent_run:agent_run/run-9");
    await user.click(screen.getByRole("button", { name: "Open trace" }));
    expect(push).toHaveBeenCalledWith("/copilot/runs/run-9");
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Same conventions as admin/agents/agents.test.tsx: graphqlRequest is routed
 * by operation name, the viewer is a full admin. Avoids the DataTable inside a
 * Card entirely (canary detail is plain Card content, not virtualized) so no
 * jsdom-scroll-height workaround is needed.
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

import EvalCanariesPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const canary = {
  id: "cc-1", urn: "wr:t-42:eval:canary/cc-1", comparisonId: "cc-1", agentKey: "claims-agent",
  candidateVersion: "v8", baselineVersion: "v7", sampleSpec: { min_samples: 200 }, mode: "paired_shadow",
  status: "collecting", report: { thresholds: {}, must_scorers: [] }, samples: 0,
  createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z",
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("mutation CreateEvalCanary")) return { createEvalCanary: canary };
    if (doc.includes("query EvalCanary")) return { evalCanary: canary };
    if (doc.includes("mutation StopEvalCanary")) return { stopEvalCanary: { ...canary, status: "expired" } };
    return {};
  };
});

describe("Eval canaries page", () => {
  it("starts a new canary comparison with the real agent/candidate/baseline versions", async () => {
    const user = userEvent.setup();
    renderWithProviders(<EvalCanariesPage />);

    await user.type(await screen.findByLabelText("Agent key"), "claims-agent");
    await user.type(screen.getByLabelText("Candidate version"), "v8");
    await user.type(screen.getByLabelText("Baseline version"), "v7");
    await user.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateEvalCanary"));
      expect(call?.vars).toMatchObject({
        input: { agentKey: "claims-agent", candidateVersion: "v8", baselineVersion: "v7" },
      });
    });

    // Starting a canary immediately looks up + displays the returned comparison.
    await screen.findByText("cc-1");
    expect(screen.getByText(/claims-agent: v8 vs v7/)).toBeInTheDocument();
  });

  it("looks up an existing comparison by id and can stop it early", async () => {
    const user = userEvent.setup();
    renderWithProviders(<EvalCanariesPage />);

    await user.type(screen.getByLabelText("Comparison id"), "cc-1");
    await user.click(screen.getByRole("button", { name: "Look up" }));

    await screen.findByText("cc-1");
    await user.click(screen.getByRole("button", { name: "Stop early" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation StopEvalCanary") && r.vars.comparisonId === "cc-1")).toBe(true);
    });
  });
});

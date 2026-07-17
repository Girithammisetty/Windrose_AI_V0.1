import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** Same conventions as admin/usage/usage.test.tsx. */
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

import AdminMemoryPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const emptyMemories = { memories: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
const emptyStats = { memoryStats: { total_records: 0 } };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Memories")) return emptyMemories;
    if (doc.includes("query MemoryStats")) return emptyStats;
    return {};
  };
});

describe("Admin Memory page — right-to-be-forgotten erasure", () => {
  it("requires typing the subject id to confirm before requesting erasure", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminMemoryPage />);

    await user.type(await screen.findByLabelText("Subject id"), "u-42");
    await user.click(screen.getByRole("button", { name: "Request erasure" }));

    const dialog = await screen.findByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "Request erasure" });
    expect(confirmBtn).toBeDisabled();

    await user.type(within(dialog).getByRole("textbox"), "u-42");
    expect(confirmBtn).toBeEnabled();

    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Memories")) return emptyMemories;
      if (doc.includes("query MemoryStats")) return emptyStats;
      if (doc.includes("mutation RequestMemoryErasure")) {
        return { requestMemoryErasure: { operationId: "op-1", status: "received", report: null, completedAt: null } };
      }
      if (doc.includes("query Erasure")) {
        return { erasure: { operationId: "op-1", status: "completed", report: { erased: 3 }, completedAt: "2026-07-12T00:00:00Z" } };
      }
      return {};
    };
    await user.click(confirmBtn);

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RequestMemoryErasure"));
      expect(call?.vars).toMatchObject({ subjectId: "u-42", subjectType: "user" });
    });
    await screen.findByText("op-1");
    await screen.findByText("completed");
  });
});

describe("Admin Memory page — browse", () => {
  it("filters memories by scope + scope ref", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query MemoryStats")) return emptyStats;
      if (doc.includes("query Memories")) {
        if (vars.scope === "workspace" && vars.scopeRef === "ws-9") {
          return {
            memories: {
              nodes: [{ id: "m-1", urn: "wr:t:memory:record/m-1", scope: "workspace", scopeRef: "ws-9",
                content: "the claim total is $4,200", confidence: 0.9, status: "active", tags: [],
                retrievalCount: 2, classifierScore: 0.1, ttlExpiresAt: null }],
              pageInfo: { nextCursor: null, hasMore: false },
            },
          };
        }
        return emptyMemories;
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<AdminMemoryPage />);

    await user.selectOptions(await screen.findByLabelText("Scope"), "workspace");
    await user.type(screen.getByLabelText("Scope ref (e.g. workspace id)"), "ws-9");

    await waitFor(() => {
      const matching = requests.filter((r) => r.doc.includes("query Memories"));
      const call = matching[matching.length - 1];
      expect(call?.vars).toMatchObject({ scope: "workspace", scopeRef: "ws-9" });
    });
  });
});

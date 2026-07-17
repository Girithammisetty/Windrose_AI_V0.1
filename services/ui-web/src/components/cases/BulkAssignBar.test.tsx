import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { useSelection } from "@/stores/ui";
import { BulkAssignBar } from "./BulkAssignBar";

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

const pushed: { title: string; variant?: string }[] = [];
vi.mock("@/stores/ui", async (importActual) => {
  const actual = await importActual<typeof import("@/stores/ui")>();
  return {
    ...actual,
    useToasts: (selector: (s: { push: (t: { title: string; variant?: string }) => void }) => unknown) =>
      selector({ push: (t) => pushed.push(t) }),
  };
});

const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};
const usersResult = {
  users: {
    nodes: [{ id: "u-1", urn: "wr:t-42:identity:user/u-1", email: "ann@x.com", fullName: "Ann" }],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  pushed.length = 0;
  useSelection.getState().clear();
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Users")) return usersResult;
    return {};
  };
});

describe("BulkAssignBar — real server-side bulk assign (no fake success)", () => {
  it("renders nothing with no selection", () => {
    const { container } = renderWithProviders(<BulkAssignBar caseCount={10} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("posts a real bulkAssignCases mutation and reports the real succeeded/failed counts", async () => {
    useSelection.getState().selectMany(["case-1", "case-2"]);
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Users")) return usersResult;
      if (doc.includes("bulkAssignCases")) {
        return { bulkAssignCases: { succeededIds: ["case-1"], failed: [{ caseId: "case-2", code: "NOT_FOUND", message: "gone" }] } };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<BulkAssignBar caseCount={10} />);

    await waitFor(() => expect(screen.getByText("2 selected")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Bulk assign" }));
    await waitFor(() => expect(screen.getByLabelText("Assign to")).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Assign to"), "u-1");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    const mutationCall = await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("bulkAssignCases"));
      expect(call).toBeTruthy();
      return call!;
    });
    expect(mutationCall.vars).toMatchObject({ caseIds: ["case-1", "case-2"], assigneeId: "u-1" });
    // The real partial-failure counts reach the user — never a blind "queued".
    await waitFor(() => expect(pushed).toEqual([{ title: "1 assigned, 1 failed", variant: "default" }]));
  });
});

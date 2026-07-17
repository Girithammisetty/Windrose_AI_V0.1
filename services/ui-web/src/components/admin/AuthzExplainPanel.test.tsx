import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

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

import { AuthzExplainPanel } from "./AuthzExplainPanel";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    return {};
  };
});

describe("AuthzExplainPanel", () => {
  it("is collapsed by default and expands on click", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AuthzExplainPanel />);

    expect(screen.queryByLabelText("Subject user id")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /Authz explain/i }));
    expect(screen.getByLabelText("Subject user id")).toBeInTheDocument();
  });

  it("submits userId+action and renders the real decision chain", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query ExplainAuthz")) {
        return {
          explainAuthz: {
            allowed: false,
            reason: "no matching grant",
            chain: [{ type: "scope_excluded", action: "case.case.delete", detail: "not in any bound role" }],
          },
        };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<AuthzExplainPanel />);

    await user.click(await screen.findByRole("button", { name: /Authz explain/i }));
    await user.type(screen.getByLabelText("Subject user id"), "u-9");
    await user.type(screen.getByLabelText("Action"), "case.case.delete");
    await user.click(screen.getByRole("button", { name: "Explain" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("query ExplainAuthz"));
      expect(call?.vars).toMatchObject({ input: { userId: "u-9", action: "case.case.delete" } });
    });
    expect(await screen.findByText("DENIED")).toBeInTheDocument();
    expect(screen.getByText(/no matching grant/)).toBeInTheDocument();
    expect(screen.getByText(/scope_excluded/)).toBeInTheDocument();
  });
});

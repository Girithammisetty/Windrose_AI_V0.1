import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import type { SemanticModelVersion } from "@/lib/graphql/types";

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

import { GraphQLRequestError } from "@/lib/graphql/client";
import { ReviewActions } from "./ReviewActions";

// renderWithProviders' SessionProvider fixes userId to "u" (src/test/utils.tsx).
const meResult = {
  me: { userId: "u", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

function version(overrides: Partial<SemanticModelVersion> = {}): SemanticModelVersion {
  return {
    id: "ver-3", urn: "wr:t-42:semantic:version/ver-3", modelId: "sm-1", versionNo: 3,
    status: "IN_REVIEW", submittedBy: "u-other", createdAt: "2026-07-12T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => (doc.includes("query Me") ? meResult : {});
});

describe("ReviewActions — governance review bar (semantic-service four-eyes)", () => {
  it("renders nothing for a version that isn't in review", async () => {
    const { container } = renderWithProviders(
      <ReviewActions modelId="sm-1" version={version({ status: "DRAFT" })} onDecided={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("hides Approve/Reject and shows a self-review notice when the viewer authored the version", async () => {
    renderWithProviders(<ReviewActions modelId="sm-1" version={version({ submittedBy: "u" })} onDecided={() => {}} />);
    expect(await screen.findByText(/You authored this version/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("shows Approve/Reject for a different reviewer and posts approveSemanticModelVersion", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation ApproveSemanticModelVersion")) {
        return { approveSemanticModelVersion: version({ status: "PUBLISHED" }) };
      }
      return {};
    };
    renderWithProviders(<ReviewActions modelId="sm-1" version={version()} onDecided={onDecided} />);
    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation ApproveSemanticModelVersion"));
      expect(call?.vars).toMatchObject({ modelId: "sm-1", versionNo: 3 });
    });
    expect(onDecided).toHaveBeenCalled();
  });

  it("requires a reject reason (client-side) before posting rejectSemanticModelVersion", async () => {
    const user = userEvent.setup();
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation RejectSemanticModelVersion")) return { rejectSemanticModelVersion: version({ status: "REJECTED" }) };
      return {};
    };
    renderWithProviders(<ReviewActions modelId="sm-1" version={version()} onDecided={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    // Reject-with-no-note is blocked client-side — no request goes out.
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.getByText("A reason is required to reject.")).toBeInTheDocument();
    expect(requests.some((r) => r.doc.includes("mutation RejectSemanticModelVersion"))).toBe(false);

    await user.type(screen.getByLabelText("Explain what needs to change…"), "missing filters");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RejectSemanticModelVersion"));
      expect(call?.vars).toMatchObject({ modelId: "sm-1", versionNo: 3, note: "missing filters" });
    });
  });

  it("surfaces the real server 403 if approve is attempted anyway (four-eyes enforced server-side too)", async () => {
    const user = userEvent.setup();
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation ApproveSemanticModelVersion")) {
        throw new GraphQLRequestError(
          [{ message: "author cannot approve their own version (SEM-FR-007)", extensions: { code: "PERMISSION_DENIED" } }],
          403,
        );
      }
      return {};
    };
    renderWithProviders(<ReviewActions modelId="sm-1" version={version()} onDecided={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByTestId("review-error")).toHaveTextContent("author cannot approve");
  });
});

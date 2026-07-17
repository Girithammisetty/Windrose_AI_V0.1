import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Exercises the promotion-approval sub-components directly with concrete
 * props (PromoteDialog / PromotionsPanel), not the top-level page — the page
 * itself resolves its route `params` via React's `use()`, which suspends on
 * an unsettled promise and needs framework-level Suspense wiring RTL doesn't
 * provide out of the box; the components below carry all the actual logic
 * under test (stage picker, approval queue, self-approval hide) and take
 * plain props, so they're the more precise + stable unit to test.
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

import { PromoteDialog, PromotionsPanel, STAGE_TRANSITIONS } from "./model-detail";
import type { ModelVersion } from "@/lib/graphql/types";

const meResult = {
  me: { userId: "u", tenantId: "t-acme", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const stagingVersion: ModelVersion = {
  modelId: "m-1", version: 2, urn: "wr:t:experiment:model_version/m-1@2", stage: "staging",
  sourceRunId: "r-1", flavor: "sklearn", mlflowModelRef: "models:/m-1/2", stageUpdatedAt: "2026-07-01T00:00:00Z",
};

const emptyPromotions = { promotions: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };

function promotionsWith(status: string, requestedBy: string) {
  return {
    promotions: {
      nodes: [
        { id: "p-1", urn: "wr:t:experiment:promotion/p-1", modelVersionId: "m-1@2", targetStage: "production",
          fromStage: "staging", status, rationale: "Beats baseline", requestedBy, viaAgent: null, decision: null,
          createdAt: "2026-07-12T00:00:00Z" },
      ],
      pageInfo: { nextCursor: null, hasMore: false },
    },
  };
}

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Promotions")) return emptyPromotions;
    return {};
  };
});

describe("STAGE_TRANSITIONS mirrors experiment-service's real transition table", () => {
  it("matches app/domain/state.py _STAGE_TRANSITIONS exactly", () => {
    expect(STAGE_TRANSITIONS).toEqual({
      none: ["staging", "archived"],
      staging: ["production", "archived"],
      production: ["archived"],
      archived: ["staging"],
    });
  });
});

describe("PromoteDialog — real stage picker (not hardcoded to production)", () => {
  it("offers only the real valid target stages for a staging version and posts the chosen one", async () => {
    const user = userEvent.setup();
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation PromoteModelVersion")) {
        return { promoteModelVersion: { promotionId: "p-new", status: "pending", operationId: "op-1" } };
      }
      return {};
    };
    renderWithProviders(<PromoteDialog modelId="m-1" version={stagingVersion} onClose={() => {}} />);

    const select = screen.getByLabelText("Target stage") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(["production", "archived"]);

    await user.selectOptions(select, "archived");
    await user.click(screen.getByRole("button", { name: "Request promotion" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation PromoteModelVersion"));
      expect(call?.vars).toMatchObject({ modelId: "m-1", version: 2, targetStage: "archived" });
    });
  });
});

describe("PromotionsPanel — four-eyes approval queue", () => {
  it("shows a pending promotion from ANOTHER requester with Approve/Reject enabled", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Promotions")) return promotionsWith("pending", "someone-else");
      return {};
    };
    renderWithProviders(<PromotionsPanel modelId="m-1" version={stagingVersion} onClose={() => {}} />);

    await screen.findByText("staging");
    expect(await screen.findByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("approving posts decision=approve for the real promotion id", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Promotions")) return promotionsWith("pending", "someone-else");
      if (doc.includes("mutation DecidePromotion")) return { decidePromotion: { id: "p-1", status: "approved" } };
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<PromotionsPanel modelId="m-1" version={stagingVersion} onClose={() => {}} />);

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation DecidePromotion"));
      expect(call?.vars).toMatchObject({ promotionId: "p-1", decision: "approve" });
    });
  });

  it("hides Approve/Reject for the viewer's OWN promotion request (self-approval forbidden)", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      // renderWithProviders seeds userId "u" — same as requestedBy here.
      if (doc.includes("query Promotions")) return promotionsWith("pending", "u");
      return {};
    };
    renderWithProviders(<PromotionsPanel modelId="m-1" version={stagingVersion} onClose={() => {}} />);

    await screen.findByText(/you requested this promotion/i);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows already-decided promotions (approved/rejected) without approve/reject controls", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Promotions")) return promotionsWith("approved", "someone-else");
      return {};
    };
    renderWithProviders(<PromotionsPanel modelId="m-1" version={stagingVersion} onClose={() => {}} />);

    await screen.findByText("staging");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

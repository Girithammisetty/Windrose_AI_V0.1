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

import { GraphQLRequestError } from "@/lib/graphql/client";
import { AuditComplianceCard } from "./AuditComplianceCard";

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

describe("AuditComplianceCard — compliance packs", () => {
  it("generates a SOC2 pack and polls the real operation until it has a download link", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation GenerateSoc2Pack")) return { generateSoc2Pack: { operationId: "op-1", status: "running", resultUrl: null, error: null } };
      if (doc.includes("query ComplianceOperation")) return { complianceOperation: { operationId: "op-1", status: "succeeded", resultUrl: "https://example/pack.zip", error: null } };
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<AuditComplianceCard />);

    await user.click(await screen.findByRole("button", { name: "Generate SOC2 pack" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation GenerateSoc2Pack"))).toBe(true);
    });
    expect(await screen.findByRole("link", { name: /Download pack/i })).toHaveAttribute("href", "https://example/pack.zip");
  });
});

describe("AuditComplianceCard — chain integrity verify", () => {
  it("shows the real pass/fail result", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation VerifyChainIntegrity")) {
        return {
          verifyChainIntegrity: {
            valid: true, eventsChecked: 128, chainHead: "abc123", manifestMatch: true,
            firstMismatchSeq: null, sealed: true,
          },
        };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<AuditComplianceCard />);

    await user.click(await screen.findByRole("button", { name: "Verify" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation VerifyChainIntegrity"))).toBe(true);
    });
    expect(await screen.findByText("VALID")).toBeInTheDocument();
    expect(screen.getByText(/128 events checked/)).toBeInTheDocument();
  });

  it("surfaces an unsealed-day 409 verbatim, not a fake result", async () => {
    handler = (doc: string) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("mutation VerifyChainIntegrity")) {
        return Promise.reject(
          new GraphQLRequestError(
            [{ message: "day not sealed yet; verify after the WORM export seals it", extensions: { code: "CONFLICT" } }],
            409,
          ),
        );
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<AuditComplianceCard />);

    await user.click(await screen.findByRole("button", { name: "Verify" }));

    expect(await screen.findByText(/day not sealed yet/)).toBeInTheDocument();
    expect(screen.queryByText("VALID")).not.toBeInTheDocument();
    expect(screen.queryByText("INVALID")).not.toBeInTheDocument();
  });
});

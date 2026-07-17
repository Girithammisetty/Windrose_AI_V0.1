import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Tier 4b: service-account lifecycle (identity-service). Conventions per
 * teams.test.tsx / runs.test.tsx: virtualized DataTable rows never mount in
 * jsdom, so assert the grid's aria-rowcount + mutation variables, and exercise
 * the one-time api_key through the create flow's SecretBanner.
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

import AdminServiceAccountsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const accountsPage = {
  serviceAccounts: {
    nodes: [
      { id: "sa-1", urn: "wr:t:identity:service_account/sa-1", name: "etl-bot",
        scopes: ["dataset.dataset.read"], expiresAt: null, lastUsedAt: null, revokedAt: null,
        createdAt: "2026-01-01T00:00:00Z", updatedAt: null },
      { id: "sa-2", urn: "wr:t:identity:service_account/sa-2", name: "old-bot",
        scopes: [], expiresAt: null, lastUsedAt: null, revokedAt: "2026-06-01T00:00:00Z",
        createdAt: "2026-01-01T00:00:00Z", updatedAt: null },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string, vars: any) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query ServiceAccounts")) return accountsPage;
    if (doc.includes("mutation CreateServiceAccount")) {
      return {
        createServiceAccount: {
          serviceAccount: { id: "sa-new", urn: "wr:t:identity:service_account/sa-new",
            name: vars.input.name, scopes: vars.input.scopes ?? [], expiresAt: vars.input.expiresAt ?? null,
            lastUsedAt: null, revokedAt: null, createdAt: "2026-07-12T00:00:00Z", updatedAt: null },
          // The one-time key — must reach the banner byte-for-byte.
          apiKey: "wr_sa_sa-new.s3cr3t-once",
        },
      };
    }
    return {};
  };
});

describe("Admin Service accounts page", () => {
  it("lists accounts (incl. a revoked one) and reflects the count in aria-rowcount", async () => {
    renderWithProviders(<AdminServiceAccountsPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Service accounts" });
      expect(grid).toHaveAttribute("aria-rowcount", "2");
    });
    expect(requests.some((r) => r.doc.includes("query ServiceAccounts"))).toBe(true);
  });

  it("creates an account with parsed scopes + expiry and shows the api_key ONCE via the banner", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminServiceAccountsPage />);
    await screen.findByRole("grid", { name: "Service accounts" });

    await user.click(screen.getByRole("button", { name: /New service account/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "ci-bot");
    // Mixed comma/space separators parse to a clean array.
    await user.type(
      within(dialog).getByLabelText(/Scopes/),
      "pipeline.run.create, dataset.dataset.read  eval.run.execute",
    );
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateServiceAccount"));
      expect(call?.vars.input).toMatchObject({
        name: "ci-bot",
        scopes: ["pipeline.run.create", "dataset.dataset.read", "eval.run.execute"],
      });
      expect(call?.vars.idempotencyKey).toBeTruthy();
    });

    // SecretBanner renders the returned apiKey verbatim (shown-once idiom,
    // same as the webhook signing secret).
    const key = await screen.findByTestId("sa-api-key");
    expect(key).toHaveTextContent("wr_sa_sa-new.s3cr3t-once");

    // Dismissing it removes the only copy from the DOM — nothing persisted.
    await user.click(screen.getByRole("button", { name: "Dismiss API key" }));
    expect(screen.queryByTestId("sa-api-key")).not.toBeInTheDocument();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";

// Dispatch graphql by operation: ME → viewer, PACK_INSTALLS → installs.
let roles: string[] = [];
let capabilities: string[] = [];
let installs: { pack: string; status: string }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: async (doc: string) => {
      if (doc.includes("packInstalls")) {
        return { packInstalls: installs.map((i, n) => ({ id: `pi-${n}`, pack: i.pack, version: "1.0.0", workspaceId: "ws", status: i.status })) };
      }
      // ME
      return { me: { userId: "u", tenantId: "t", type: "user", scopes: [], roles, capabilities, capsDegraded: false } };
    },
  };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { HelpHome } from "./HelpHome";

beforeEach(() => {
  roles = [];
  capabilities = [];
  installs = [];
});

describe("HelpHome", () => {
  it("shows the card-disputes pack guide and highlights the signed-in persona", async () => {
    installs = [{ pack: "card-disputes", status: "installed" }];
    roles = ["Fraud Investigator"];

    renderWithProviders(<HelpHome />);

    await waitFor(() => expect(screen.getByTestId("help-pack-card")).toBeInTheDocument());
    // pack detected + named
    expect(screen.getByText(/Card Disputes — your solution guide/)).toBeInTheDocument();
    // all five personas rendered
    expect(screen.getByTestId("help-personas").querySelectorAll("span").length).toBeGreaterThanOrEqual(5);
    // persona banner names the signed-in role
    expect(screen.getByTestId("help-persona-banner")).toHaveTextContent("Fraud Investigator");
    // a non-admin does NOT see the admin section
    expect(screen.queryByTestId("help-admin-section")).toBeNull();
    // platform capability cards render
    expect(screen.getByText("Approvals and four-eyes")).toBeInTheDocument();
  });

  it("falls back gracefully when the installed pack has no overlay", async () => {
    installs = [{ pack: "some-unbuilt-pack", status: "installed" }];
    roles = ["Some Role"];

    renderWithProviders(<HelpHome />);

    await waitFor(() => expect(screen.getByTestId("help-pack-card")).toBeInTheDocument());
    expect(screen.getByTestId("help-pack-missing")).toHaveTextContent(/some-unbuilt-pack/);
    // still shows platform guides
    expect(screen.getByText("Approvals and four-eyes")).toBeInTheDocument();
  });

  it("shows the admin guide section for an admin", async () => {
    installs = [{ pack: "card-disputes", status: "installed" }];
    roles = ["Admin"];
    capabilities = ["*"];

    renderWithProviders(<HelpHome />);

    await waitFor(() => expect(screen.getByTestId("help-admin-section")).toBeInTheDocument());
    expect(screen.getByText("Platform admin: overview")).toBeInTheDocument();
  });
});

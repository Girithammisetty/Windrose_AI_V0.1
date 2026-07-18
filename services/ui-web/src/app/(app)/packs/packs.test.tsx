import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Capability Packs surface (BRD 23). Verifies the catalog render, the dry-run
 * plan (create | exists | deferred), and the install → ledger flow, plus the
 * installed-packs section + uninstall.
 */
let handler: (doc: string, vars: any) => any = () => ({});
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return { ...actual, graphqlRequest: async (doc: string, vars: any) => handler(doc, vars) };
});

import PacksPage from "./page";

const me = {
  me: { userId: "u", tenantId: "t", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

function pack() {
  return {
    name: "card-disputes", version: "1.0.0", description: "Card dispute adjudication.",
    publisherName: "Windrose Inc.", categories: ["banking", "cards"], regulatory: ["reg_e"],
    components: [{ kind: "dispositions", count: 5 }, { kind: "roles", count: 5 }],
    deferredKinds: ["guardrails", "case_schemas"],
  };
}

function base(doc: string): any {
  if (doc.includes("query Me")) return me;
  if (doc.includes("query PackInstalls")) return { packInstalls: [] };
  if (doc.includes("query Packs")) return { packs: [pack()] };
  if (doc.includes("query PackDetail")) return { pack: { ...pack(), deferred: [{ kind: "guardrails", reason: "OPA policy materialization not exposed yet." }] } };
  return {};
}

beforeEach(() => { handler = base; });

describe("PacksPage (BRD 23 capability packs)", () => {
  it("renders the catalog with a pack card", async () => {
    renderWithProviders(<PacksPage />);
    const card = await screen.findByTestId("pack-card");
    expect(within(card).getByText("card-disputes")).toBeInTheDocument();
    expect(within(card).getByText(/10 components · 2 deferred kinds/)).toBeInTheDocument();
  });

  it("runs a dry-run plan showing create/exists/deferred", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("mutation PlanPackInstall")) {
        expect(vars.pack).toBe("card-disputes");
        return { planPackInstall: { pack: "card-disputes", version: "1.0.0", workspaceId: "ws",
          plan: [
            { kind: "dispositions", identity: "d", name: "file_chargeback", action: "create", detail: null },
            { kind: "semantic_models", identity: "s", action: "deferred", detail: "needs approver" },
          ] } };
      }
      return base(doc);
    };
    const user = userEvent.setup();
    renderWithProviders(<PacksPage />);
    const card = await screen.findByTestId("pack-card");
    await user.click(within(card).getByRole("button", { name: /Details & install/i }));
    await user.click(await within(card).findByRole("button", { name: /Dry-run plan/i }));
    const plan = await within(card).findByTestId("pack-plan");
    expect(within(plan).getByText(/1 create · 0 already present · 1 deferred/)).toBeInTheDocument();
  });

  it("installs and shows the materialization ledger", async () => {
    handler = (doc: string) => {
      if (doc.includes("mutation InstallPack")) {
        return { installPack: { id: "i-1", pack: "card-disputes", version: "1.0.0", workspaceId: "ws",
          status: "installed", summary: { created: 10 },
          ledger: [
            { id: "l1", kind: "roles", identity: "AP Analyst", action: "create", reversible: true, tombstoned: false, origin: "pack:card-disputes@1.0.0:roles/x", detail: null, targetUrn: null, targetId: "r1" },
            { id: "l2", kind: "dispositions", identity: "file_chargeback", action: "create", reversible: false, tombstoned: false, origin: "o", detail: null, targetUrn: null, targetId: null },
          ] } };
      }
      return base(doc);
    };
    const user = userEvent.setup();
    renderWithProviders(<PacksPage />);
    const card = await screen.findByTestId("pack-card");
    await user.click(within(card).getByRole("button", { name: /Details & install/i }));
    await user.click(await within(card).findByRole("button", { name: /Install into this workspace/i }));
    const ledger = await within(card).findByTestId("pack-ledger");
    expect(within(ledger).getByText(/2 objects materialized \(1 reversible\)/)).toBeInTheDocument();
  });

  it("shows installed packs with an uninstall control", async () => {
    handler = (doc: string) => {
      if (doc.includes("query PackInstalls")) {
        return { packInstalls: [{ id: "i-1", pack: "card-disputes", version: "1.0.0", workspaceId: "ws",
          status: "installed", summary: { created: 10, deferred: 11 }, createdBy: "u", createdAt: null }] };
      }
      return base(doc);
    };
    renderWithProviders(<PacksPage />);
    const row = await screen.findByTestId("pack-install-row");
    expect(within(row).getByText("card-disputes")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /Uninstall/i })).toBeInTheDocument();
  });
});

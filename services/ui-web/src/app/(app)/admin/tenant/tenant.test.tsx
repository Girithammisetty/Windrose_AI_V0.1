import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** Route graphqlRequest by operation name to a per-test handler. */
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

import AdminTenantPage from "./page";

const meResult = {
  me: { userId: "u", tenantId: "t-1", workspaceId: "ws", type: "user", scopes: [],
    roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};
const tenantResult = {
  tenant: {
    id: "t-1", name: "acme", displayName: "Acme Claims Co", ownerEmail: "o@acme.co",
    subdomain: "acme", status: "active", createdAt: null, tier: "shared", cloud: "aws",
    platformVersion: "1.0", autoUpgrade: true, modules: [], quotas: null, embedConfig: null,
  },
};
const labelsResult = {
  tenantLabels: [{ key: "nav.cases", value: "AP Exceptions" }],
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Tenant ") || doc.includes("query Tenant(")) return tenantResult;
    if (doc.includes("query TenantLabels")) return labelsResult;
    if (doc.includes("query TenantIdp")) return { tenantIdp: { configured: false, enabled: false } };
    return {};
  };
});

describe("Admin tenant — display labels editor (inc18)", () => {
  it("lists the tenant label overrides", async () => {
    renderWithProviders(<AdminTenantPage />);
    expect(await screen.findByText("AP Exceptions")).toBeInTheDocument();
    // "nav.cases" also appears in the descriptive copy, so assert it renders at least once.
    expect(screen.getAllByText("nav.cases").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Delete override nav.cases" })).toBeInTheDocument();
    expect(requests.some((r) => r.doc.includes("query TenantLabels"))).toBe(true);
  });

  it("setTenantLabel sends the composed key + value", async () => {
    handler = (doc: string, vars: any) => {
      if (doc.includes("query Me")) return meResult;
      if (doc.includes("query Tenant ") || doc.includes("query Tenant(")) return tenantResult;
      if (doc.includes("query TenantLabels")) return labelsResult;
      if (doc.includes("query TenantIdp")) return { tenantIdp: { configured: false, enabled: false } };
      if (doc.includes("mutation SetTenantLabel")) {
        return { setTenantLabel: [...labelsResult.tenantLabels, { key: vars.key, value: vars.value }] };
      }
      return {};
    };
    const user = userEvent.setup();
    renderWithProviders(<AdminTenantPage />);
    await screen.findByText("AP Exceptions");

    await user.type(screen.getByLabelText("i18n key"), "nav.datasets");
    await user.type(screen.getByLabelText("Display value"), "Data feeds");
    await user.click(screen.getByRole("button", { name: "Set label" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation SetTenantLabel"));
      expect(call?.vars).toEqual({ key: "nav.datasets", value: "Data feeds" });
    });
  });
});

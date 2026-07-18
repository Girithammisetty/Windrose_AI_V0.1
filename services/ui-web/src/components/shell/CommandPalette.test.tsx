import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { CommandPalette, CMDK_EVENT } from "./CommandPalette";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

let handler: (doc: string, vars: any) => any = () => ({});
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return { ...actual, graphqlRequest: async (doc: string, vars: any) => handler(doc, vars) };
});

const meResult = {
  me: { userId: "u", tenantId: "t", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

beforeEach(() => {
  push.mockClear();
  handler = (doc: string, vars: any) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Datasets")) {
      expect(vars.q).toBe("claim");
      return { datasets: { nodes: [{ id: "ds-9", urn: "u", name: "auto_claims", tags: [] }], pageInfo: { hasMore: false } } };
    }
    if (doc.includes("query Dashboards")) return { dashboards: { nodes: [], pageInfo: { hasMore: false } } };
    if (doc.includes("query DecisionModels")) return { decisionModels: [] };
    return {};
  };
});

function openWithMeta() {
  fireEvent.keyDown(window, { key: "k", metaKey: true });
}

describe("CommandPalette (⌘K global search)", () => {
  it("is hidden until opened, then opens on ⌘K", async () => {
    renderWithProviders(<CommandPalette />);
    expect(screen.queryByRole("dialog", { name: /command palette/i })).not.toBeInTheDocument();
    openWithMeta();
    expect(await screen.findByRole("dialog", { name: /command palette/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Command palette search")).toBeInTheDocument();
  });

  it("opens on the CMDK event (the top-bar trigger)", async () => {
    renderWithProviders(<CommandPalette />);
    window.dispatchEvent(new Event(CMDK_EVENT));
    expect(await screen.findByRole("dialog", { name: /command palette/i })).toBeInTheDocument();
  });

  it("offers capability-gated navigation and jumps on Enter", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette />);
    await waitFor(() => expect(screen.queryByText(/^\*$/)).not.toBeInTheDocument()); // let viewer load
    openWithMeta();
    const input = await screen.findByLabelText("Command palette search");
    await user.type(input, "dashboards");
    const opt = await screen.findByRole("option", { name: /Dashboards/i });
    expect(opt).toBeInTheDocument();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboards"));
  });

  it("searches datasets when the query is 2+ chars", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CommandPalette />);
    openWithMeta();
    const input = await screen.findByLabelText("Command palette search");
    await user.type(input, "claim");
    expect(await screen.findByRole("option", { name: /auto_claims/i })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /auto_claims/i }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/data/datasets/ds-9"));
  });

  it("closes on Escape", async () => {
    renderWithProviders(<CommandPalette />);
    openWithMeta();
    const input = await screen.findByLabelText("Command palette search");
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /command palette/i })).not.toBeInTheDocument());
  });
});

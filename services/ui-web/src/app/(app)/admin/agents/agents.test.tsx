import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Same conventions as admin/usage/usage.test.tsx: graphqlRequest is routed by
 * operation name, the viewer is a full admin. The kill-switch create flow adds
 * a confirmPhrase gate (ConfirmDialog) on top of the normal form submit — the
 * dialog's typed-confirmation input has no explicit label (see
 * ConfirmDialog.tsx), so it's targeted as the sole textbox inside the dialog.
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

import AdminAgentsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const emptyAgentKills = { agentKillSwitches: [] };
const emptyToolKills = { toolKillSwitches: [] };

const agentKill = {
  id: "k-1", target: "AGENT", scope: "agent_version_tenant", agentKey: "case-triage", toolId: null,
  version: null, tenantId: "t-42", active: true, reason: "INC-1", setBy: "user:u-1", createdAt: "2026-07-12T00:00:00Z",
};
const toolKill = {
  id: "tk-1", target: "TOOL", scope: "tool", toolId: "pipeline.launch_run", agentKey: null,
  version: null, tenantId: "t-42", active: true, reason: "TPL-INC-1", setBy: "user:u-1", createdAt: "2026-07-12T00:00:00Z",
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query AgentKillSwitches")) return emptyAgentKills;
    if (doc.includes("query ToolKillSwitches")) return emptyToolKills;
    if (doc.includes("mutation CreateAgentKillSwitch")) return { createAgentKillSwitch: agentKill };
    if (doc.includes("mutation DeleteAgentKillSwitch")) return { deleteAgentKillSwitch: { id: "k-1", active: false } };
    if (doc.includes("mutation CreateToolKillSwitch")) return { createToolKillSwitch: toolKill };
    if (doc.includes("mutation DeleteToolKillSwitch")) return { deleteToolKillSwitch: { id: "tk-1", active: false } };
    return {};
  };
});

describe("Admin Agents page — agent kill switches", () => {
  it("requires typing the agent key to confirm before creating a kill switch", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminAgentsPage />);

    await user.click((await screen.findAllByRole("button", { name: "New kill switch" }))[0]);
    await user.type(screen.getByLabelText("Agent key"), "case-triage");
    await user.type(screen.getByLabelText("Reason (required)"), "INC-1");
    await user.click(screen.getAllByRole("button", { name: "New kill switch" })[0]);

    const dialog = await screen.findByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "New kill switch" });
    expect(confirmBtn).toBeDisabled();

    await user.type(within(dialog).getByRole("textbox"), "case-triage");
    expect(confirmBtn).toBeEnabled();
    await user.click(confirmBtn);

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateAgentKillSwitch"));
      expect(call?.vars).toMatchObject({ agentKey: "case-triage", reason: "INC-1", scope: "agent_version_tenant" });
    });
  });

  it("lifts an active agent kill switch through the destructive confirm dialog", async () => {
    // DataTable virtualizes against the real scroll container height, which
    // jsdom always reports as 0 (see usage.test.tsx's header comment) — so a
    // pre-populated row is never clickable here. Selection is instead driven
    // through the create flow (onCreate sets `selected` from the mutation
    // result directly), exactly like BudgetsCard's own tests do.
    const user = userEvent.setup();
    renderWithProviders(<AdminAgentsPage />);

    await user.click((await screen.findAllByRole("button", { name: "New kill switch" }))[0]);
    await user.type(screen.getByLabelText("Agent key"), "case-triage");
    await user.type(screen.getByLabelText("Reason (required)"), "INC-1");
    await user.click(screen.getAllByRole("button", { name: "New kill switch" })[0]);
    const createDialog = await screen.findByRole("dialog");
    await user.type(within(createDialog).getByRole("textbox"), "case-triage");
    await user.click(within(createDialog).getByRole("button", { name: "New kill switch" }));
    await screen.findByRole("heading", { name: "case-triage" });

    await user.click(screen.getByRole("button", { name: "Lift" }));
    const liftDialog = await screen.findByRole("dialog");
    await user.click(within(liftDialog).getByRole("button", { name: "Lift" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation DeleteAgentKillSwitch") && r.vars.killId === "k-1")).toBe(true);
    });
  });
});

describe("Admin Agents page — tool kill switches", () => {
  it("requires typing the tool id to confirm before creating a kill switch", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminAgentsPage />);

    const newButtons = await screen.findAllByRole("button", { name: "New kill switch" });
    await user.click(newButtons[1]); // tool card is the second "New kill switch" button
    await user.type(screen.getByLabelText("Tool id"), "pipeline.launch_run");
    await user.type(screen.getByLabelText("Reason (required)"), "TPL-INC-1");
    await user.click(screen.getAllByRole("button", { name: "New kill switch" })[1]);

    const dialog = await screen.findByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", { name: "New kill switch" });
    expect(confirmBtn).toBeDisabled();

    await user.type(within(dialog).getByRole("textbox"), "pipeline.launch_run");
    expect(confirmBtn).toBeEnabled();
    await user.click(confirmBtn);

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateToolKillSwitch"));
      expect(call?.vars).toMatchObject({ toolId: "pipeline.launch_run", reason: "TPL-INC-1", scope: "tool_tenant" });
    });
  });
});

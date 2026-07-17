import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Same conventions as admin/usage/usage.test.tsx: graphqlRequest is routed by
 * operation name, the viewer is a full admin (every Can gate passes), and the
 * dashboard/report doubles return real-shaped payloads.
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

import DashboardReportsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const dashboardsResult = {
  dashboards: {
    nodes: [{ id: "dash-1", urn: "wr:t:chart:dashboard/dash-1", title: "Claims overview", module: "insights" }],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

function subscription(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "rep-1", urn: "wr:t:notification:report_subscription/rep-1", dashboardId: "dash-1", workspaceId: "ws-9",
    name: "Weekly claims summary", recipients: ["manager@demo.windrose"], cadence: "weekly", sendHour: 8,
    sendWeekday: 1, timezone: "UTC", format: "html", enabled: true, lastSentAt: null, lastStatus: "",
    lastError: "", createdBy: "manager@demo.windrose", createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z",
    ...overrides,
  };
}

let listed: ReturnType<typeof subscription>[] = [];

beforeEach(() => {
  requests.length = 0;
  listed = [subscription()];
  handler = (doc: string, vars: any) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Dashboards")) return dashboardsResult;
    if (doc.includes("query ReportSubscriptions")) {
      return { reportSubscriptions: { nodes: listed, pageInfo: { nextCursor: null, hasMore: false } } };
    }
    if (doc.includes("mutation CreateReportSubscription")) {
      const created = subscription({ id: "rep-new", name: vars.input.name, recipients: vars.input.recipients, cadence: vars.input.cadence });
      listed = [...listed, created];
      return { createReportSubscription: created };
    }
    if (doc.includes("mutation PauseReportSubscription")) {
      const updated = subscription({ enabled: !vars.paused });
      listed = listed.map((s) => (s.id === vars.id ? updated : s));
      return { pauseReportSubscription: updated };
    }
    if (doc.includes("mutation DeleteReportSubscription")) {
      listed = listed.filter((s) => s.id !== vars.id);
      return { deleteReportSubscription: true };
    }
    if (doc.includes("mutation TriggerReportSubscription")) {
      return { triggerReportSubscription: true };
    }
    return {};
  };
});

describe("Team reports page (notification-service report subscriptions)", () => {
  it("lists existing subscriptions with dashboard name, cadence and recipients", async () => {
    renderWithProviders(<DashboardReportsPage />);
    expect(await screen.findByText("Weekly claims summary")).toBeInTheDocument();
    expect(screen.getByText("Claims overview")).toBeInTheDocument();
    expect(screen.getByText("manager@demo.windrose")).toBeInTheDocument();
  });

  it("creates a report subscription and posts real recipients/cadence to the BFF", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DashboardReportsPage />);
    await screen.findByText("Weekly claims summary");

    await user.click(screen.getByRole("button", { name: /New subscription/i }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText("Dashboard"), "dash-1");
    await user.type(within(dialog).getByLabelText("Subscription name"), "Monthly ops digest");
    await user.type(within(dialog).getByLabelText("Recipients"), "a@demo.windrose, b@demo.windrose");
    await user.selectOptions(within(dialog).getByLabelText("Cadence"), "daily");
    await user.click(within(dialog).getByRole("button", { name: "New subscription" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateReportSubscription"));
      expect(call?.vars.input).toMatchObject({
        dashboardId: "dash-1",
        name: "Monthly ops digest",
        recipients: ["a@demo.windrose", "b@demo.windrose"],
        cadence: "daily",
      });
    });
    expect(await screen.findByText("Monthly ops digest")).toBeInTheDocument();
  });

  it("pauses a subscription via pauseReportSubscription(enabled=false)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DashboardReportsPage />);
    await screen.findByText("Weekly claims summary");

    await user.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation PauseReportSubscription"));
      expect(call?.vars).toMatchObject({ id: "rep-1", paused: true });
    });
  });

  it("deletes a subscription through the confirm dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DashboardReportsPage />);
    await screen.findByText("Weekly claims summary");

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation DeleteReportSubscription") && r.vars.id === "rep-1")).toBe(true);
    });
  });

  it("triggers an immediate send via triggerReportSubscription (real Temporal Schedule.Trigger)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DashboardReportsPage />);
    await screen.findByText("Weekly claims summary");

    await user.click(screen.getByRole("button", { name: "Send now" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation TriggerReportSubscription") && r.vars.id === "rep-1")).toBe(true);
    });
    expect(await screen.findByTestId("report-banner")).toHaveTextContent("manager@demo.windrose");
  });
});

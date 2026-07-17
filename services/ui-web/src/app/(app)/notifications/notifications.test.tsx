import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** graphqlRequest routed by operation name; viewer is a full admin. DataTable
 * is virtualized and jsdom has no layout, so assertions target request
 * variables + the grid's aria-rowcount (repo convention, see runs.test.tsx). */
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

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import NotificationsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const inbox = {
  notifications: {
    nodes: [
      { id: "n-1", urn: "wr:t:notification:notification/n-1", eventType: "case.assigned.v1", severityClass: "action",
        title: "Case assigned to you", body: null, resourceUrn: null, deepLink: "/cases/c-1", readAt: null,
        createdAt: "2026-07-12T01:00:00Z" },
      { id: "n-2", urn: "wr:t:notification:notification/n-2", eventType: "ingestion.completed.v1", severityClass: "info",
        title: "Ingestion finished", body: null, resourceUrn: null, deepLink: null, readAt: "2026-07-12T02:00:00Z",
        createdAt: "2026-07-12T00:30:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const prefs = {
  notificationPreferences: {
    channelOverrides: { "case.assigned.v1": ["email"] },
    mutes: { event_types: [] },
    quietHours: null,
    digestConfig: {},
    updatedAt: "2026-07-12T00:00:00Z",
  },
};

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Notifications(")) return inbox;
    if (doc.includes("query NotificationPreferences")) return prefs;
    if (doc.includes("mutation MarkAllNotificationsRead")) return { markAllNotificationsRead: 1 };
    if (doc.includes("mutation UpdateNotificationPreferences")) return { updateNotificationPreferences: prefs.notificationPreferences };
    return {};
  };
});

describe("Notifications page (inbox + preferences)", () => {
  it("lists the real inbox and reflects the row count in the grid", async () => {
    renderWithProviders(<NotificationsPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Notifications" });
      expect(grid).toHaveAttribute("aria-rowcount", "2");
    });
    expect(requests.some((r) => r.doc.includes("query Notifications("))).toBe(true);
  });

  it("threads the unread-only filter into the notifications query variables", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NotificationsPage />);
    await screen.findByRole("grid", { name: "Notifications" });

    await user.click(screen.getByLabelText("Unread only"));
    await waitFor(() => {
      const call = requests.filter((r) => r.doc.includes("query Notifications(")).at(-1);
      expect(call?.vars?.unread).toBe(true);
    });
  });

  it("fires the real mark-all-read mutation", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NotificationsPage />);
    await screen.findByRole("grid", { name: "Notifications" });

    await user.click(screen.getByRole("button", { name: /Mark all read/ }));
    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation MarkAllNotificationsRead"))).toBe(true);
    });
  });

  it("saves preferences with the edited channel overrides", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NotificationsPage />);
    const overrides = await screen.findByLabelText("Channel overrides");

    await user.clear(overrides);
    // userEvent.type treats { and [ as key descriptors — paste instead.
    await user.click(overrides);
    await user.paste('{"case.assigned.v1": ["inapp"]}');
    await user.click(screen.getByRole("button", { name: "Save preferences" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation UpdateNotificationPreferences"));
      expect(call?.vars?.input?.channelOverrides).toEqual({ "case.assigned.v1": ["inapp"] });
    });
  });
});

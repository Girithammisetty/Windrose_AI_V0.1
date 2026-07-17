import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/** graphqlRequest routed by operation name; viewer is a full admin. DataTable
 * rows never materialize in jsdom (virtualized) — assertions target request
 * variables, aria-rowcount, and non-table surfaces like the shown-once secret
 * banner (repo convention, see runs.test.tsx / agents.test.tsx). */
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

import AdminNotificationsPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const rules = {
  notificationRules: {
    nodes: [
      { id: "r-1", scope: "user", subjectType: "user", subjectId: "u-1", eventTypes: ["case.assigned.v1"],
        resourceFilter: null, channels: ["inapp"], digestEnabled: false, digestWindow: "1h", active: true,
        createdBy: "u-1", createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z" },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

const webhook = {
  id: "wh-new", url: "https://hooks.example.com/x", eventTypes: ["case.assigned.v1"], active: true,
  verifiedAt: "2026-07-12T00:00:00Z", circuitState: "closed", consecutiveFailures: 0,
  createdBy: "u-1", createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z",
  secrets: [{ version: 1, secret: "shown-once-secret", createdAt: "2026-07-12T00:00:00Z", expiresAt: null }],
};

const emptyPage = { nodes: [], pageInfo: { nextCursor: null, hasMore: false } };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query NotificationRules")) return rules;
    if (doc.includes("query NotificationWebhooks")) return { notificationWebhooks: emptyPage };
    if (doc.includes("query NotificationDeliveryStats")) {
      return { notificationDeliveryStats: { window: "24h0m0s", byChannel: { email: { sent: 5 } } } };
    }
    if (doc.includes("query EmailSuppressions")) return { emailSuppressions: [] };
    if (doc.includes("mutation CreateNotificationRule")) {
      return { createNotificationRule: rules.notificationRules.nodes[0] };
    }
    if (doc.includes("mutation CreateNotificationWebhook")) return { createNotificationWebhook: webhook };
    return {};
  };
});

describe("Admin notification settings page", () => {
  it("lists rules + delivery stats from the real queries", async () => {
    renderWithProviders(<AdminNotificationsPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Subscription rules" });
      expect(grid).toHaveAttribute("aria-rowcount", "1");
    });
    expect(await screen.findByTestId("delivery-stats")).toHaveTextContent('"sent": 5');
    expect(requests.some((r) => r.doc.includes("query NotificationWebhooks"))).toBe(true);
    expect(requests.some((r) => r.doc.includes("query EmailSuppressions"))).toBe(true);
  });

  it("creates a rule with the entered event types + channels", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminNotificationsPage />);
    await screen.findByRole("grid", { name: "Subscription rules" });

    await user.click(screen.getByRole("button", { name: "New rule" }));
    await user.type(screen.getByLabelText("Rule event types"), "case.assigned.v1, case.resolved.v1");
    await user.clear(screen.getByLabelText("Rule channels"));
    await user.type(screen.getByLabelText("Rule channels"), "inapp, email");
    await user.click(screen.getByRole("button", { name: "New rule" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateNotificationRule"));
      expect(call?.vars?.input).toMatchObject({
        eventTypes: ["case.assigned.v1", "case.resolved.v1"],
        channels: ["inapp", "email"],
      });
    });
  });

  it("creates a webhook and surfaces the signing secret ONCE", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminNotificationsPage />);
    await screen.findByRole("grid", { name: "Subscription rules" });

    await user.click(screen.getByRole("button", { name: "New webhook" }));
    await user.type(screen.getByLabelText("Webhook URL"), "https://hooks.example.com/x");
    await user.type(screen.getByLabelText("Webhook event types"), "case.assigned.v1");
    await user.click(screen.getByRole("button", { name: "New webhook" }));

    // The mutation carried the real input…
    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateNotificationWebhook"));
      expect(call?.vars?.input).toMatchObject({ url: "https://hooks.example.com/x", eventTypes: ["case.assigned.v1"] });
    });
    // …and the v1 secret from the response is displayed (shown-once banner).
    expect(await screen.findByTestId("webhook-secret")).toHaveTextContent("shown-once-secret");

    // Dismissing removes it — it is not re-fetchable from the list selection.
    await user.click(screen.getByRole("button", { name: "Dismiss secret" }));
    expect(screen.queryByTestId("webhook-secret")).toBeNull();
  });
});

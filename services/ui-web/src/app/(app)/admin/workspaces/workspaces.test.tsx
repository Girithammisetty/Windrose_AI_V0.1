import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Tier 4b: workspace lifecycle (rbac-service). Virtualized DataTable rows never
 * mount in jsdom (see teams.test.tsx), so selection is driven through the
 * create-workspace flow — the detail panel then exercises the same archive /
 * grants surfaces a row click would.
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

import AdminWorkspacesPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const wsFields = {
  urn: "wr:t:rbac:workspace/ws-new", description: "", public: false,
  archived: false, archivedAt: null, createdBy: "u-1",
  createdAt: "2026-07-12T00:00:00Z", updatedAt: "2026-07-12T00:00:00Z",
};

const emptyConn = { nodes: [], pageInfo: { nextCursor: null, hasMore: false } };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string, vars: any) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Workspaces")) return { workspaces: emptyConn };
    if (doc.includes("query Groups")) return { groups: emptyConn };
    if (doc.includes("query Users")) return { users: emptyConn };
    if (doc.includes("query ContentGrants")) {
      return {
        contentGrants: [
          { subjectType: "user", subjectId: "u-1", level: "owner", provenance: "implicit_creator",
            via: null, grantId: "gr-1", workspaceId: "ws-new" },
        ],
      };
    }
    if (doc.includes("mutation CreateWorkspace")) {
      return { createWorkspace: { id: "ws-new", name: vars.input.name, ...wsFields } };
    }
    if (doc.includes("mutation ArchiveWorkspace")) {
      return { archiveWorkspace: { id: vars.id, name: "Claims Q3", ...wsFields,
        archived: true, archivedAt: "2026-07-12T01:00:00Z" } };
    }
    if (doc.includes("mutation RestoreWorkspace")) {
      return { restoreWorkspace: { id: vars.id, name: "Claims Q3", ...wsFields } };
    }
    return {};
  };
});

async function createWorkspace(user: ReturnType<typeof userEvent.setup>) {
  // Two "New workspace" affordances exist on an empty list (header action +
  // empty-state CTA) — either opens the same dialog.
  await user.click(screen.getAllByRole("button", { name: /New workspace/ })[0]);
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "Claims Q3");
  await user.click(within(dialog).getByRole("button", { name: "Create" }));
  // The created workspace is selected: the detail panel heading appears.
  expect(await screen.findByText("Claims Q3")).toBeInTheDocument();
}

describe("Admin Workspaces page", () => {
  it("archives the selected workspace: destructive confirm, then archiveWorkspace fires with the row id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminWorkspacesPage />);
    await screen.findByText("No workspaces yet.");
    await createWorkspace(user);

    await user.click(screen.getByRole("button", { name: "Archive" }));
    const dialog = await screen.findByRole("dialog");
    // The blast radius is spelled out before the destructive confirm.
    expect(within(dialog).getByText(/stops resolving for non-admin members/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Archive" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation ArchiveWorkspace"));
      expect(call?.vars.id).toBe("ws-new");
    });

    // The panel flips to the archived state and offers Restore.
    expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
  });

  it("looks up effective access by resource URN and threads it into contentGrants", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminWorkspacesPage />);
    await screen.findByText("No workspaces yet.");
    await createWorkspace(user);

    await user.type(screen.getByLabelText("Resource URN"), "wr:t-42:dataset:dataset/d1");
    await user.click(screen.getByRole("button", { name: "Look up" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("query ContentGrants"));
      expect(call?.vars.resourceUrn).toBe("wr:t-42:dataset:dataset/d1");
    });
    // The implicit-creator row renders with its provenance; being non-direct,
    // it carries no Remove control.
    expect(await screen.findByText(/owner · implicit_creator/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });
});

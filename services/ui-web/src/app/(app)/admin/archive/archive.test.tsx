import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";
import { GraphQLRequestError } from "@/lib/graphql/client";

/** Same conventions as the other admin page tests in this repo (teams, usage):
 * graphqlRequest routed by operation name, admin viewer so every Can-gated
 * control renders, selection driven outside the (jsdom-unrenderable) DataTable
 * rows where the flow requires picking a specific row. A handler may throw a
 * GraphQLRequestError to simulate a real downstream error surfacing honestly. */
let handler: (doc: string, vars: any) => any = () => ({});
const requests: { doc: string; vars: any }[] = [];
vi.mock("@/lib/graphql/client", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/graphql/client")>();
  return {
    ...actual,
    graphqlRequest: (doc: string, vars: any) => {
      requests.push({ doc, vars });
      try {
        return Promise.resolve(handler(doc, vars));
      } catch (e) {
        return Promise.reject(e);
      }
    },
  };
});

import AdminArchivePage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

const emptyWorkspaces = { workspaces: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
const emptyDashboards = { archivedDashboards: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };
const emptyExperiments = { archivedExperiments: { nodes: [], pageInfo: { nextCursor: null, hasMore: false } } };

beforeEach(() => {
  requests.length = 0;
  handler = (doc: string, vars: any) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Workspaces")) return emptyWorkspaces;
    if (doc.includes("query ArchivedDashboards")) return emptyDashboards;
    if (doc.includes("query ArchivedExperiments")) return emptyExperiments;
    if (doc.includes("mutation ArchiveDataset")) {
      if (vars.id === "boom") {
        throw new GraphQLRequestError([{ message: "dataset not found", extensions: { code: "NOT_FOUND" } }], 404);
      }
      return { archiveDataset: true };
    }
    if (doc.includes("mutation RestoreDataset")) {
      return {
        restoreDataset: {
          id: vars.id, urn: `wr:t:dataset:dataset/${vars.id}`, name: "Claims (restored)", description: null,
          status: "ready", tags: [], rowCount: null, createdAt: null, archived: false, archivedAt: null,
        },
      };
    }
    return {};
  };
});

describe("Admin Archive page", () => {
  it("requests archived-only lists for workspaces, dashboards, and experiments", async () => {
    renderWithProviders(<AdminArchivePage />);
    await waitFor(() => {
      const wsCall = requests.find((r) => r.doc.includes("query Workspaces"));
      expect(wsCall?.vars.archived).toBe("only");
      expect(requests.some((r) => r.doc.includes("query ArchivedDashboards"))).toBe(true);
      expect(requests.some((r) => r.doc.includes("query ArchivedExperiments"))).toBe(true);
    });
  });

  it("archives a dataset by id via the honest write-only form", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminArchivePage />);

    await user.type(await screen.findByLabelText("Dataset id to archive"), "ds-42");
    await user.click(screen.getByRole("button", { name: "Archive" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation ArchiveDataset"));
      expect(call?.vars).toMatchObject({ id: "ds-42" });
    });
    expect(await screen.findByText("Archived dataset ds-42.")).toBeInTheDocument();
  });

  it("surfaces a real downstream error for archiveDataset honestly", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminArchivePage />);

    await user.type(await screen.findByLabelText("Dataset id to archive"), "boom");
    await user.click(screen.getByRole("button", { name: "Archive" }));

    expect(await screen.findByText("dataset not found")).toBeInTheDocument();
  });

  it("restores a dataset by id and shows its (possibly renamed) new name", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminArchivePage />);

    await user.type(await screen.findByLabelText("Dataset id to restore"), "ds-7");
    await user.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RestoreDataset"));
      expect(call?.vars).toMatchObject({ id: "ds-7" });
    });
    expect(await screen.findByText('Restored dataset as "Claims (restored)".')).toBeInTheDocument();
  });
});

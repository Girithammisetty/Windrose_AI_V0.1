import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/utils";

/**
 * Tier 4b: roles admin (rbac-service /roles). Same conventions as
 * teams.test.tsx: DataTable rows never materialize under jsdom (virtualizer
 * measures a 0-height container), so these tests assert the grid's logical
 * aria-rowcount + request variables, and drive detail-panel selection through
 * the create flow rather than row clicks.
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

import AdminRolesPage from "./page";

const meResult = {
  me: { userId: "u-1", tenantId: "t-42", type: "user", scopes: [], roles: ["Admin"], capabilities: ["*"], capsDegraded: false },
};

// One system + one custom role — what rbac's seeded tenant actually mixes.
const rolesPage = {
  roles: {
    nodes: [
      { id: "r-sys", name: "Admin", system: true, version: 1, actions: [], createdAt: null, updatedAt: null },
      { id: "r-custom", name: "Claims Triage", system: false, version: 2,
        actions: ["case.case.read", "case.case.update"], createdAt: null, updatedAt: null },
    ],
    pageInfo: { nextCursor: null, hasMore: false },
  },
};

let createRoleResponse: any;

beforeEach(() => {
  requests.length = 0;
  createRoleResponse = {
    createRole: { id: "r-new", name: "Fraud Reviewer", system: false, version: 1,
      actions: ["case.case.read"], createdAt: null, updatedAt: null },
  };
  handler = (doc: string) => {
    if (doc.includes("query Me")) return meResult;
    if (doc.includes("query Roles")) return rolesPage;
    if (doc.includes("mutation CreateRole")) return createRoleResponse;
    if (doc.includes("mutation RenameRole")) {
      return { renameRole: { id: "r-new", name: "Fraud Reviewer II", system: false, version: 1,
        actions: ["case.case.read"], createdAt: null, updatedAt: null } };
    }
    if (doc.includes("mutation SetRoleActions")) {
      return { setRoleActions: { id: "r-new", name: "Fraud Reviewer", system: false, version: 2,
        actions: ["case.case.read", "case.case.export"], createdAt: null, updatedAt: null } };
    }
    if (doc.includes("mutation UpdateRole")) {
      return { updateRole: { id: "r-new", name: "Fraud Reviewer II", system: false, version: 2,
        actions: ["case.case.read", "case.case.export"], createdAt: null, updatedAt: null } };
    }
    if (doc.includes("mutation DeleteRole")) return { deleteRole: true };
    return {};
  };
});

async function createRole(user: ReturnType<typeof userEvent.setup>, actionsText = "case.case.read") {
  await user.click(screen.getByRole("button", { name: "New role" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "Fraud Reviewer");
  await user.type(within(dialog).getByLabelText("Actions (one per line)"), actionsText);
  await user.click(within(dialog).getByRole("button", { name: "Create" }));
}

describe("Admin Roles page", () => {
  it("lists roles from the real query and reflects the count in the grid's aria-rowcount", async () => {
    renderWithProviders(<AdminRolesPage />);
    await waitFor(() => {
      const grid = screen.getByRole("grid", { name: "Roles" });
      expect(grid).toHaveAttribute("aria-rowcount", "2");
    });
    expect(requests.some((r) => r.doc.includes("query Roles"))).toBe(true);
  });

  it("createRole sends the parsed one-action-per-line list and selects the new role", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });

    await createRole(user, "case.case.read\ncase.case.update\n\n  case.case.export  ");

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation CreateRole"));
      // Blank lines dropped, whitespace trimmed — the wire list is clean.
      expect(call?.vars.input).toEqual({
        name: "Fraud Reviewer",
        actions: ["case.case.read", "case.case.update", "case.case.export"],
      });
      expect(call?.vars.idempotencyKey).toBeTruthy();
    });

    // The created (custom) role is selected: mutation controls render.
    expect(await screen.findByRole("button", { name: "Rename" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit actions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("renames the selected custom role via renameRole", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });
    await createRole(user);

    await user.click(await screen.findByRole("button", { name: "Rename" }));
    const input = screen.getByLabelText("Edit role name");
    await user.clear(input);
    await user.type(input, "Fraud Reviewer II");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation RenameRole"));
      expect(call?.vars).toMatchObject({ id: "r-new", name: "Fraud Reviewer II" });
    });
  });

  it("edits the action set via setRoleActions with the prefilled textarea parsed per line", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });
    await createRole(user);

    await user.click(await screen.findByRole("button", { name: "Edit actions" }));
    const textarea = screen.getByLabelText("Edit role actions");
    // Prefilled from role.actions.
    expect(textarea).toHaveValue("case.case.read");
    await user.type(textarea, "\ncase.case.export");
    await user.click(screen.getByRole("button", { name: "Save actions" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation SetRoleActions"));
      expect(call?.vars).toMatchObject({ id: "r-new", actions: ["case.case.read", "case.case.export"] });
    });
  });

  it("edits name + actions together in one dialog via the atomic updateRole PATCH", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });
    await createRole(user); // creates + selects the custom "Fraud Reviewer" role

    // The unified Edit control opens the authoring dialog in edit mode.
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Edit role")).toBeInTheDocument();
    // Prefilled from the selected role (name + newline-joined actions).
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Fraud Reviewer");
    expect(within(dialog).getByLabelText("Actions (one per line)")).toHaveValue("case.case.read");

    const nameInput = within(dialog).getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Fraud Reviewer II");
    await user.type(within(dialog).getByLabelText("Actions (one per line)"), "\ncase.case.export");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      const call = requests.find((r) => r.doc.includes("mutation UpdateRole"));
      expect(call?.vars).toMatchObject({
        id: "r-new",
        input: { name: "Fraud Reviewer II", actions: ["case.case.read", "case.case.export"] },
      });
      expect(call?.vars.idempotencyKey).toBeTruthy();
    });
  });

  it("system roles render the system badge and NO mutation controls", async () => {
    // Rows never materialize under jsdom, so drive the system-role detail the
    // pragmatic way: have the create mutation return a system role and let the
    // page select it — the detail panel then exercises the exact same
    // role.system branch a row click would.
    createRoleResponse = {
      createRole: { id: "r-sys", name: "Admin", system: true, version: 1,
        actions: ["case.case.read"], createdAt: null, updatedAt: null },
    };
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });
    await createRole(user);

    expect(await screen.findByText(/reject every mutation \(409 SYSTEM_IMMUTABLE\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rename" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit actions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("clones a role: prefills the create dialog with 'Copy of <name>' + the source actions", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });
    // Create + select a role so the detail panel (with Clone) renders.
    await createRole(user);

    await user.click(await screen.findByRole("button", { name: /Clone/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Clone role")).toBeInTheDocument();
    // Seeded from the selected role (name "Fraud Reviewer", actions case.case.read).
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Copy of Fraud Reviewer");
    expect(within(dialog).getByLabelText("Actions (one per line)")).toHaveValue("case.case.read");

    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => {
      const calls = requests.filter((r) => r.doc.includes("mutation CreateRole"));
      const clone = calls[calls.length - 1];
      expect(clone?.vars.input).toEqual({
        name: "Copy of Fraud Reviewer",
        actions: ["case.case.read"],
      });
    });
  });

  it("deletes a custom role through the destructive confirm dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminRolesPage />);
    await screen.findByRole("grid", { name: "Roles" });
    await createRole(user);

    await user.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(requests.some((r) => r.doc.includes("mutation DeleteRole") && r.vars.id === "r-new")).toBe(true);
    });
    // Selection cleared back to the placeholder.
    expect(await screen.findByText(/Select a role to see its actions/)).toBeInTheDocument();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import type { Viewer } from "@/lib/graphql/types";

// Mock the current route + the viewer query the capability hook reads.
let mockPath = "/";
vi.mock("next/navigation", () => ({ usePathname: () => mockPath }));

let mockViewer: Viewer | undefined;
vi.mock("@/lib/graphql/hooks", () => ({
  useMe: () => ({ data: mockViewer ? { me: mockViewer } : undefined, isLoading: false, isError: false }),
}));

import { Sidebar } from "@/components/shell/Sidebar";
import { RouteGuard } from "@/components/authz/RouteGuard";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";

function viewer(roles: string[], capabilities: string[]): Viewer {
  return { userId: "u", tenantId: "t", tenantName: "Tenant T", workspaceId: "w", workspaceName: "Default use case", type: "user", scopes: [], roles, capabilities, capsDegraded: false };
}

const ADJUSTER = viewer(
  ["Case Analyst"],
  ["case.case.read", "ai.proposal.read", "chart.dashboard.read", "rbac.workspace.read"],
);
const MANAGER = viewer(
  ["Case Manager"],
  [
    "case.case.read",
    "case.case.assign",
    "ai.proposal.read",
    "chart.dashboard.read",
    "chart.dashboard.create",
    "chart.chart.create",
    "usage.report.read",
  ],
);
const DATASCIENTIST = viewer(
  ["Model Builder", "Data User"],
  [
    "dataset.dataset.list",
    "experiment.experiment.read",
    "chart.dashboard.read",
    "chart.dashboard.create",
    "chart.chart.create",
    "ingestion.connection.read",
    "ingestion.connection.create",
    "pipeline.template.read",
    "pipeline.template.create",
  ],
);
const ADMIN = viewer(["Admin"], ["*"]);

const navKeys = () =>
  Array.from(document.querySelectorAll("[data-nav]")).map((el) => el.getAttribute("data-nav"));

const navGroups = () =>
  Array.from(document.querySelectorAll("[data-nav-group]")).map((el) => el.getAttribute("data-nav-group"));

beforeEach(() => {
  mockPath = "/";
  mockViewer = undefined;
});

describe("Sidebar renders only the nav a persona's capabilities unlock", () => {
  it("adjuster: no admin, no data, no ml nav", () => {
    mockViewer = ADJUSTER;
    renderWithProviders(<Sidebar />);
    const keys = navKeys();
    expect(keys).toContain("cases");
    expect(keys).toContain("inbox");
    expect(keys).not.toContain("admin");
    expect(keys).not.toContain("data");
    expect(keys).not.toContain("ml");
  });

  it("datascientist: data + ml + data-sources, but not admin user-management", () => {
    mockViewer = DATASCIENTIST;
    renderWithProviders(<Sidebar />);
    const keys = navKeys();
    expect(keys).toContain("data");
    expect(keys).toContain("sources");
    expect(keys).toContain("pipelines");
    expect(keys).toContain("ml");
    expect(keys).not.toContain("admin");
    expect(keys).not.toContain("cases");
  });

  it("adjuster does not see the Data Sources nav (ingestion capability absent)", () => {
    mockViewer = ADJUSTER;
    renderWithProviders(<Sidebar />);
    expect(navKeys()).not.toContain("sources");
  });

  it("admin: sees every nav item", () => {
    mockViewer = ADMIN;
    renderWithProviders(<Sidebar />);
    const keys = navKeys();
    expect(keys).toEqual(expect.arrayContaining(["cases", "inbox", "data", "ml", "dashboards", "reports", "admin"]));
  });

  it("shows only the section headers whose group has a visible item", () => {
    mockViewer = ADJUSTER;
    renderWithProviders(<Sidebar />);
    // adjuster has casework (cases/inbox) + insights (dashboards) but no data/ml.
    expect(navGroups()).toContain("casework");
    expect(navGroups()).toContain("insights");
    expect(navGroups()).not.toContain("data");
    expect(navGroups()).not.toContain("ml");
  });

  it("datascientist sees the Data + ML section headers", () => {
    mockViewer = DATASCIENTIST;
    renderWithProviders(<Sidebar />);
    expect(navGroups()).toEqual(expect.arrayContaining(["data", "ml", "insights"]));
    expect(navGroups()).not.toContain("casework");
  });

  it("capsDegraded: shows the 'permissions unavailable' notice while the nav stays fail-closed", () => {
    mockViewer = { ...viewer([], []), capsDegraded: true };
    renderWithProviders(<Sidebar />);
    expect(document.querySelector("[data-caps-degraded]")).toBeInTheDocument();
    expect(navKeys()).not.toContain("admin"); // still fail-closed, just not silent
  });

  it("no degradation notice on a healthy viewer", () => {
    mockViewer = ADJUSTER;
    renderWithProviders(<Sidebar />);
    expect(document.querySelector("[data-caps-degraded]")).not.toBeInTheDocument();
  });
});

describe("Can-gated dashboard authoring controls (chart.dashboard.create)", () => {
  const CreateControl = () => (
    <Can gate={FEATURE_GATES.createDashboard}>
      <button data-testid="create-dashboard">Create dashboard</button>
    </Can>
  );

  it("shows the create-dashboard control to datascientist + manager", () => {
    mockViewer = DATASCIENTIST;
    renderWithProviders(<CreateControl />);
    expect(screen.getByTestId("create-dashboard")).toBeInTheDocument();
  });

  it("shows the create-dashboard control to manager", () => {
    mockViewer = MANAGER;
    renderWithProviders(<CreateControl />);
    expect(screen.getByTestId("create-dashboard")).toBeInTheDocument();
  });

  it("hides the create-dashboard control from adjuster (read-only dashboards)", () => {
    mockViewer = ADJUSTER;
    renderWithProviders(<CreateControl />);
    expect(screen.queryByTestId("create-dashboard")).not.toBeInTheDocument();
    // adjuster still sees the dashboards nav (read gate)
    renderWithProviders(<Sidebar />);
    expect(navKeys()).toContain("dashboards");
  });
});

describe("RouteGuard blocks a disallowed route", () => {
  it("adjuster on /admin sees a no-access state, not the admin page", () => {
    mockPath = "/admin/users";
    mockViewer = ADJUSTER;
    renderWithProviders(
      <RouteGuard>
        <div data-testid="admin-page">SECRET ADMIN</div>
      </RouteGuard>,
    );
    expect(screen.getByTestId("no-access")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-page")).not.toBeInTheDocument();
  });

  it("admin on /admin sees the page", () => {
    mockPath = "/admin/users";
    mockViewer = ADMIN;
    renderWithProviders(
      <RouteGuard>
        <div data-testid="admin-page">ADMIN OK</div>
      </RouteGuard>,
    );
    expect(screen.getByTestId("admin-page")).toBeInTheDocument();
    expect(screen.queryByTestId("no-access")).not.toBeInTheDocument();
  });

  it("datascientist on /cases is blocked", () => {
    mockPath = "/cases";
    mockViewer = DATASCIENTIST;
    renderWithProviders(
      <RouteGuard>
        <div data-testid="cases-page">CASES</div>
      </RouteGuard>,
    );
    expect(screen.getByTestId("no-access")).toBeInTheDocument();
    expect(screen.queryByTestId("cases-page")).not.toBeInTheDocument();
  });

  it("public route (home) renders for everyone, even before capabilities load", () => {
    mockPath = "/";
    mockViewer = undefined; // still loading
    renderWithProviders(
      <RouteGuard>
        <div data-testid="home-page">HOME</div>
      </RouteGuard>,
    );
    expect(screen.getByTestId("home-page")).toBeInTheDocument();
  });
});

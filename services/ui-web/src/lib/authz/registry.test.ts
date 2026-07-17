import { describe, it, expect } from "vitest";
import {
  NAV_ITEMS,
  FEATURE_GATES,
  allows,
  gateForPath,
  toCapabilitySet,
  cap,
  role,
  publicGate,
  ADMIN_ROLE,
} from "./registry";

/** Persona capability fixtures mirroring the seeded rbac projections. */
const ADJUSTER = toCapabilitySet({
  roles: ["Case Analyst"],
  capabilities: [
    "case.case.read",
    "case.disposition.read",
    "ai.proposal.read",
    "chart.dashboard.read",
    "rbac.workspace.read",
  ],
});
const MANAGER = toCapabilitySet({
  roles: ["Case Manager"],
  capabilities: [
    "case.case.read",
    "case.case.assign",
    "ai.proposal.read",
    "chart.dashboard.read",
    // manager authors oversight dashboards + charts
    "chart.dashboard.create",
    "chart.chart.create",
    "usage.report.read",
    // manager also subscribes dashboards to scheduled report digests (NOTIF-FR-060)
    "notification.report.read",
    "notification.report.create",
  ],
});
const DATASCIENTIST = toCapabilitySet({
  roles: ["Model Builder", "Data User"],
  capabilities: [
    "dataset.dataset.list",
    "experiment.experiment.read",
    // Model Builder authors experiments + submits inference jobs, but does NOT
    // hold the four-eyes promotion-approval grant (experiment.promotion.approve).
    "experiment.experiment.create",
    "experiment.model.read",
    "inference.job.read",
    "inference.job.create",
    "chart.dashboard.read",
    // datascientist authors dashboards + charts
    "chart.dashboard.create",
    "chart.chart.create",
    // data-integration / datascientist manages data-source connections
    "ingestion.connection.read",
    "ingestion.connection.create",
    // datascientist builds & runs no-code pipelines
    "pipeline.template.read",
    "pipeline.template.create",
    // Model Builder also subscribes dashboards to scheduled report digests (NOTIF-FR-060)
    "notification.report.read",
    "notification.report.create",
  ],
});
const ADMIN = toCapabilitySet({ roles: [ADMIN_ROLE], capabilities: ["*"] });

const navKeys = (caps: ReturnType<typeof toCapabilitySet>) =>
  NAV_ITEMS.filter((item) => allows(item.gate, caps)).map((i) => i.key);

describe("role-based nav gating (four personas => four different apps)", () => {
  it("adjuster: cases/inbox/dashboards, but NOT admin, data, ml or reports", () => {
    const keys = navKeys(ADJUSTER);
    expect(keys).toEqual(expect.arrayContaining(["home", "cases", "inbox", "dashboards", "copilot"]));
    expect(keys).not.toContain("admin");
    expect(keys).not.toContain("data");
    expect(keys).not.toContain("ml");
    expect(keys).not.toContain("reports");
  });

  it("datascientist: data + ml + data-sources, but NOT admin/user-management, cases or inbox", () => {
    const keys = navKeys(DATASCIENTIST);
    expect(keys).toEqual(expect.arrayContaining(["home", "data", "sources", "pipelines", "ml", "dashboards", "copilot"]));
    expect(keys).not.toContain("admin");
    expect(keys).not.toContain("cases");
    expect(keys).not.toContain("inbox");
    // and the admin-user-management gate is denied
    expect(allows(cap("identity.user.list"), DATASCIENTIST)).toBe(false);
  });

  it("Data Sources nav is gated on the ingestion capability (adjuster/manager hidden)", () => {
    expect(navKeys(ADJUSTER)).not.toContain("sources");
    expect(navKeys(MANAGER)).not.toContain("sources");
    expect(navKeys(DATASCIENTIST)).toContain("sources");
    expect(navKeys(ADMIN)).toContain("sources");
  });

  it("Pipelines nav is gated on the pipeline capability (adjuster/manager hidden)", () => {
    expect(navKeys(ADJUSTER)).not.toContain("pipelines");
    expect(navKeys(MANAGER)).not.toContain("pipelines");
    expect(navKeys(DATASCIENTIST)).toContain("pipelines");
    expect(navKeys(ADMIN)).toContain("pipelines");
  });

  it("manager: adjuster's set + oversight (reports), still no admin/data/ml", () => {
    const keys = navKeys(MANAGER);
    expect(keys).toContain("reports");
    expect(keys).toContain("cases");
    expect(keys).not.toContain("admin");
    expect(keys).not.toContain("data");
    // manager gets bulk-assign (a write adjuster lacks)
    expect(allows(cap("case.case.assign"), MANAGER)).toBe(true);
    expect(allows(cap("case.case.assign"), ADJUSTER)).toBe(false);
  });

  it("admin: sees everything via the '*' wildcard / Admin role", () => {
    const keys = navKeys(ADMIN);
    expect(keys).toEqual(NAV_ITEMS.map((i) => i.key));
    expect(allows(role(ADMIN_ROLE), ADMIN)).toBe(true);
    expect(allows(cap("anything.at.all"), ADMIN)).toBe(true);
  });

  it("the four personas produce four DISTINCT navs", () => {
    const navs = [navKeys(ADJUSTER), navKeys(MANAGER), navKeys(DATASCIENTIST), navKeys(ADMIN)].map((n) =>
      n.sort().join(","),
    );
    expect(new Set(navs).size).toBe(4);
  });
});

describe("dashboard authoring feature gate (chart.dashboard.create)", () => {
  it("manager + datascientist can create dashboards/charts; adjuster cannot", () => {
    expect(allows(FEATURE_GATES.createDashboard, MANAGER)).toBe(true);
    expect(allows(FEATURE_GATES.createDashboard, DATASCIENTIST)).toBe(true);
    // adjuster holds only chart.dashboard.read → sees the nav but not the create controls
    expect(allows(FEATURE_GATES.createDashboard, ADJUSTER)).toBe(false);
    expect(navKeys(ADJUSTER)).toContain("dashboards");
  });

  it("the create gate resolves to the chart.dashboard.create capability", () => {
    expect(FEATURE_GATES.createDashboard).toEqual(cap("chart.dashboard.create"));
  });
});

describe("ML feature gates (Model Builder persona)", () => {
  it("datascientist can create experiments + inference jobs", () => {
    expect(allows(FEATURE_GATES.createExperiment, DATASCIENTIST)).toBe(true);
    expect(allows(FEATURE_GATES.createInferenceJob, DATASCIENTIST)).toBe(true);
  });

  it("datascientist CANNOT promote models (no seeded role holds the promote grant)", () => {
    // The promote-request guard experiment.model.update (and the four-eyes approval
    // grant experiment.promotion.approve) are held by NO seeded persona — Model
    // Builder holds only experiment.model.read/list. The control is hidden until the
    // grants are added (see RBAC note).
    expect(allows(FEATURE_GATES.promoteModel, DATASCIENTIST)).toBe(false);
    expect(allows(FEATURE_GATES.promoteModel, ADJUSTER)).toBe(false);
    // admin passes everything via the wildcard.
    expect(allows(FEATURE_GATES.promoteModel, ADMIN)).toBe(true);
  });

  it("the ML gates resolve to the real rbac capabilities", () => {
    expect(FEATURE_GATES.createExperiment).toEqual(cap("experiment.experiment.create"));
    expect(FEATURE_GATES.createInferenceJob).toEqual(cap("inference.job.create"));
    expect(FEATURE_GATES.promoteModel).toEqual(cap("experiment.model.update"));
  });
});

describe("route guard gates", () => {
  it("maps admin subroutes to the Admin role gate (blocks non-admins)", () => {
    const g = gateForPath("/admin/users");
    expect(allows(g, ADJUSTER)).toBe(false);
    expect(allows(g, DATASCIENTIST)).toBe(false);
    expect(allows(g, ADMIN)).toBe(true);
  });

  it("blocks adjuster from /data but allows datascientist", () => {
    const g = gateForPath("/data/datasets");
    expect(allows(g, ADJUSTER)).toBe(false);
    expect(allows(g, DATASCIENTIST)).toBe(true);
  });

  it("/data/connections needs the ingestion capability (longer-prefix override of /data)", () => {
    const g = gateForPath("/data/connections");
    expect(g).toEqual(cap("ingestion.connection.read"));
    expect(allows(g, DATASCIENTIST)).toBe(true);
    expect(allows(g, ADJUSTER)).toBe(false);
  });

  it("/data/pipelines needs the pipeline capability (longer-prefix override of /data)", () => {
    const g = gateForPath("/data/pipelines");
    expect(g).toEqual(cap("pipeline.template.read"));
    expect(allows(g, DATASCIENTIST)).toBe(true);
    expect(allows(g, ADJUSTER)).toBe(false);
  });

  it("blocks datascientist from /cases but allows adjuster", () => {
    const g = gateForPath("/cases/abc-123");
    expect(allows(g, DATASCIENTIST)).toBe(false);
    expect(allows(g, ADJUSTER)).toBe(true);
  });

  it("home and copilot are public (every persona)", () => {
    expect(gateForPath("/")).toEqual(publicGate);
    expect(gateForPath("/copilot")).toEqual(publicGate);
  });
});

describe("fail-safe defaults", () => {
  it("an empty/unknown capability set allows only public gates", () => {
    const empty = toCapabilitySet({});
    expect(allows(publicGate, empty)).toBe(true);
    expect(allows(cap("case.case.read"), empty)).toBe(false);
    expect(allows(role(ADMIN_ROLE), empty)).toBe(false);
    expect(navKeys(empty)).toEqual(["home", "copilot"]);
  });
});

describe("nav grouping invariant (Sidebar header logic depends on it)", () => {
  // The Sidebar emits a section header whenever `group` changes between adjacent
  // visible items, so all items of one group MUST be contiguous in NAV_ITEMS —
  // otherwise a group's header would render twice (once per run of items).
  it("every group's items are contiguous — no group appears in two runs", () => {
    const runs: string[] = [];
    let prev: string | undefined = "<<start>>";
    for (const item of NAV_ITEMS) {
      const g = item.group ?? "__ungrouped__";
      if (g !== prev) runs.push(g);
      prev = g;
    }
    const grouped = runs.filter((g) => g !== "__ungrouped__");
    expect(grouped).toEqual([...new Set(grouped)]); // each group is one run only
  });
});

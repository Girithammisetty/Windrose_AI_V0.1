import { describe, it, expect } from "vitest";
import {
  resolveArticle,
  siblings,
  packGuide,
  primaryPackName,
  personaForViewer,
  articleAppliesToRole,
  roleMatches,
  personaSlug,
  platformArticles,
  adminArticles,
} from "./registry";

describe("help registry — articles", () => {
  it("resolves platform and admin articles by slug", () => {
    expect(resolveArticle("approvals")?.title).toMatch(/four-eyes/i);
    expect(resolveArticle("admin-audit")?.audience).toBe("admin");
    expect(resolveArticle("does-not-exist")).toBeNull();
  });

  it("keeps admin articles out of the end-user platform list", () => {
    expect(platformArticles().some((a) => a.audience === "admin")).toBe(false);
    expect(adminArticles().every((a) => a.audience === "admin")).toBe(true);
  });

  it("gives prev/next within the same area, same admin side", () => {
    const cockpit = resolveArticle("case-cockpit")!;
    const { prev, next } = siblings(cockpit);
    // casework order: worklist(1) → case-cockpit(2) → evidence(3)
    expect(prev?.slug).toBe("worklist");
    expect(next?.slug).toBe("evidence");
  });
});

describe("help registry — pack scoping", () => {
  it("returns the card-disputes overlay with its five personas", () => {
    const g = packGuide("card-disputes")!;
    expect(g.displayName).toBe("Card Disputes");
    expect(g.personas.map((p) => p.roleName)).toEqual([
      "Dispute Intake Analyst",
      "Fraud Investigator",
      "Chargeback Specialist",
      "Dispute Operations Manager",
      "Dispute Compliance Auditor",
    ]);
  });

  it("returns null for a pack with no overlay (graceful fallback)", () => {
    expect(packGuide("investigation-framework")).toBeNull(); // library pack, no surface
    expect(packGuide("some-unbuilt-pack")).toBeNull();
    expect(packGuide(null)).toBeNull();
  });

  it("picks the installed vertical pack, ignoring library packs and uninstalled rows", () => {
    expect(
      primaryPackName([
        { pack: "investigation-framework", status: "installed" },
        { pack: "banking-aml", status: "installed" },
      ]),
    ).toBe("banking-aml");
    expect(
      primaryPackName([{ pack: "card-disputes", status: "uninstalled" }]),
    ).toBeNull();
    expect(primaryPackName([])).toBeNull();
  });
});

describe("help registry — persona matching", () => {
  it("matches the signed-in role to a persona, case-insensitively", () => {
    const g = packGuide("card-disputes")!;
    const p = personaForViewer(g, ["fraud investigator"]);
    expect(p?.roleName).toBe("Fraud Investigator");
    expect(personaForViewer(g, ["Some Other Role"])).toBeNull();
  });

  it("gates article relevance by audience", () => {
    const everyone = resolveArticle("worklist")!;
    const managerOnly = resolveArticle("decision-tables")!;
    expect(articleAppliesToRole(everyone, [])).toBe(true);
    expect(articleAppliesToRole(managerOnly, ["Dispute Intake Analyst"])).toBe(false);
    expect(articleAppliesToRole(managerOnly, ["Dispute Operations Manager"])).toBe(true);
  });

  it("roleMatches trims and lowercases; personaSlug is anchor-safe", () => {
    expect(roleMatches("Dispute Operations Manager", ["  dispute operations manager "])).toBe(true);
    expect(personaSlug("Dispute Operations Manager")).toBe("dispute-operations-manager");
  });
});

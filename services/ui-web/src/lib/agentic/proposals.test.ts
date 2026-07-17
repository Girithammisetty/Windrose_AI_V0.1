import { describe, it, expect } from "vitest";
import {
  isDestructiveTool,
  isBulkApprovable,
  resolveBulkSelection,
  summarizeByTool,
  riskOf,
  BULK_APPROVE_CAP,
} from "./proposals";
import type { Proposal } from "@/lib/graphql/types";

function p(partial: Partial<Proposal>): Proposal {
  return {
    id: partial.id ?? "p",
    urn: "urn",
    agentKey: "triage",
    tool: partial.tool ?? "assign_case",
    // A genuine inbox proposal carries a server-authoritative tier. Default to the
    // bulk-approvable write-proposal tier; individual tests override as needed.
    riskTier: partial.riskTier ?? "write-proposal",
    argsDiff: partial.argsDiff ?? {},
    rationale: null,
    affectedUrns: [],
    predictedEffect: null,
    status: partial.status ?? "PENDING",
    decision: null,
    createdAt: null,
  };
}

describe("isDestructiveTool", () => {
  it.each(["delete_dataset", "purge_cases", "drop_table", "revoke_access", "archive_case", "overwriteConfig"])(
    "flags %s as destructive",
    (tool) => expect(isDestructiveTool(tool)).toBe(true),
  );
  it("treats benign tools as non-destructive", () => {
    expect(isDestructiveTool("assign_case")).toBe(false);
    expect(isDestructiveTool("tag_dataset")).toBe(false);
    expect(isDestructiveTool(null)).toBe(false);
  });
});

describe("riskOf (fail closed)", () => {
  it("high for destructive tools", () => {
    expect(riskOf({ tool: "delete_x", riskTier: "read", argsDiff: {} }).risk).toBe("high");
  });
  it("high when an explicit risk tier is set above low", () => {
    expect(riskOf({ tool: "assign", argsDiff: { riskTier: "high" } }).risk).toBe("high");
  });
  it("high when the server tier is write-direct or admin", () => {
    expect(riskOf({ tool: "assign", riskTier: "write-direct", argsDiff: {} }).risk).toBe("high");
    expect(riskOf({ tool: "assign", riskTier: "admin", argsDiff: {} }).risk).toBe("high");
  });
  it("FAILS CLOSED: missing/unknown risk classification is high, not low", () => {
    expect(riskOf({ tool: "assign", argsDiff: {} }).risk).toBe("high"); // no tier anywhere
    expect(riskOf({ tool: "assign", riskTier: "unknown", argsDiff: {} }).risk).toBe("high");
  });
  it("low only for an explicit read / write-proposal / low tier", () => {
    expect(riskOf({ tool: "assign", riskTier: "read", argsDiff: {} }).risk).toBe("low");
    expect(riskOf({ tool: "assign", riskTier: "write-proposal", argsDiff: {} }).risk).toBe("low");
    expect(riskOf({ tool: "assign", argsDiff: { riskTier: "low" } }).risk).toBe("low");
  });
});

describe("bulk-approve slip-through defence (SAFETY, fail closed)", () => {
  // Constructed slip-through: a tool name that dodges the destructive regex but
  // writes directly. Under the old default-low logic this became bulk-approvable.
  const slip = { tool: "applyDisposition", argsDiff: { writeDirect: true }, status: "PENDING" as const };

  it("the writeDirect slip-through proposal is HIGH and NOT bulk-approvable", () => {
    expect(riskOf(slip).risk).toBe("high");
    expect(isBulkApprovable(slip)).toBe(false);
  });

  it("the same tool with only a server write-direct tier is also excluded", () => {
    const viaTier = { tool: "applyDisposition", riskTier: "write-direct", argsDiff: {}, status: "PENDING" as const };
    expect(isBulkApprovable(viaTier)).toBe(false);
  });

  it("a genuinely low/read proposal is still bulk-approvable", () => {
    expect(isBulkApprovable({ tool: "assign_case", riskTier: "read", argsDiff: {}, status: "PENDING" })).toBe(true);
    expect(isBulkApprovable({ tool: "assign_case", riskTier: "write-proposal", argsDiff: {}, status: "PENDING" })).toBe(true);
  });

  it("excludes the slip-through from a mixed bulk selection", () => {
    const proposals = [
      p({ id: "good", tool: "assign_case", riskTier: "read" }),
      { id: "slip", ...slip },
    ];
    const res = resolveBulkSelection(proposals, new Set(["good", "slip"]));
    expect(res.approvable).toEqual(["good"]);
    expect(res.excluded).toEqual(["slip"]);
  });
});

describe("bulk-approve destructive exclusion (AC-5, BR-3)", () => {
  it("excludes destructive proposals from a bulk selection by construction", () => {
    const proposals = [
      p({ id: "a", tool: "assign_case" }),
      p({ id: "b", tool: "tag_case" }),
      p({ id: "c", tool: "reassign_case" }),
      p({ id: "d", tool: "delete_case" }), // destructive
    ];
    const res = resolveBulkSelection(proposals, new Set(["a", "b", "c", "d"]));
    expect(res.approvable.sort()).toEqual(["a", "b", "c"]);
    expect(res.excluded).toEqual(["d"]);
  });

  it("isBulkApprovable is false for destructive and for already-decided", () => {
    expect(isBulkApprovable(p({ tool: "delete_x" }))).toBe(false);
    expect(isBulkApprovable(p({ tool: "assign", status: "APPROVED" }))).toBe(false);
    expect(isBulkApprovable(p({ tool: "assign" }))).toBe(true);
  });

  it("caps the approvable set at the bulk cap", () => {
    const many = Array.from({ length: BULK_APPROVE_CAP + 10 }, (_, i) => p({ id: `x${i}`, tool: "assign" }));
    const res = resolveBulkSelection(many, new Set(many.map((m) => m.id)));
    expect(res.approvable).toHaveLength(BULK_APPROVE_CAP);
    expect(res.capped).toBe(true);
  });

  it("summarizes counts by tool for the confirmation dialog", () => {
    const proposals = [p({ id: "a", tool: "assign" }), p({ id: "b", tool: "assign" }), p({ id: "c", tool: "tag" })];
    const summary = summarizeByTool(proposals, ["a", "b", "c"]);
    expect(summary).toContainEqual({ tool: "assign", count: 2 });
    expect(summary).toContainEqual({ tool: "tag", count: 1 });
  });
});

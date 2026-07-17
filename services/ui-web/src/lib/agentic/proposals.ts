/**
 * Proposal risk classification + bulk-approve gating (UI-FR-033, BR-3, AC-5).
 * PURE and unit-tested. The destructive-tool exclusion is enforced HERE (single
 * source of truth) so the inbox can never construct a bulk selection that
 * includes a destructive or high-risk proposal.
 */
import type { Proposal } from "@/lib/graphql/types";

/** Tools whose effect is irreversible / destructive → never bulk-approvable. */
const DESTRUCTIVE_TOOL_PATTERNS = [
  /delete/i,
  /destroy/i,
  /drop/i,
  /purge/i,
  /remove/i,
  /revoke/i,
  /deactivate/i,
  /archive/i,
  /overwrite/i,
  /truncate/i,
];

export const BULK_APPROVE_CAP = 50; // UI-FR-033

export function isDestructiveTool(tool?: string | null): boolean {
  if (!tool) return false;
  return DESTRUCTIVE_TOOL_PATTERNS.some((re) => re.test(tool));
}

export interface RiskInfo {
  risk: "low" | "high";
  reason?: string;
}

/**
 * The ONLY tiers a proposal may carry and still be bulk-approvable. Everything
 * else — write-direct, admin, an unknown string, or NO tier at all — is high.
 * This is the fail-closed allowlist: bulk-approve is opt-in per tier, never
 * opt-out. (Tool-plane tiers mirror agent-runtime: read | write-proposal |
 * write-direct | admin.)
 */
const BULK_APPROVABLE_TIERS = new Set(["low", "read", "write-proposal"]);

/**
 * Derive risk, FAIL CLOSED. A proposal is treated as HIGH (and excluded from
 * bulk-approve) unless it presents a server-authoritative low/read tier. Sources,
 * in order: destructive tool name → high; an explicit write-direct signal → high;
 * the server `riskTier`/`tier` field (BFF passthrough of the tool-plane tier);
 * a tier carried inside argsDiff. A missing/unknown classification is HIGH.
 */
export function riskOf(p: Pick<Proposal, "tool" | "argsDiff" | "riskTier">): RiskInfo {
  if (isDestructiveTool(p.tool)) return { risk: "high", reason: "destructive tool" };

  const args = p.argsDiff as any;
  // An explicit write-direct / write-bypass signal always forces high, even if a
  // tool name dodges the destructive regex (constructed slip-through defence).
  if (args?.writeDirect === true || args?.write_direct === true) {
    return { risk: "high", reason: "write-direct" };
  }

  // Prefer the server-authoritative tier (BFF Proposal.riskTier), then argsDiff.
  const rawTier =
    (p as any).riskTier ??
    (p as any).tier ??
    args?.riskTier ??
    args?.risk_tier ??
    args?.tier;
  const tier = rawTier == null ? null : String(rawTier).toLowerCase();

  if (tier == null) return { risk: "high", reason: "unknown risk tier" };
  if (BULK_APPROVABLE_TIERS.has(tier)) return { risk: "low" };
  return { risk: "high", reason: `risk tier: ${tier}` };
}

export function isBulkApprovable(p: Pick<Proposal, "tool" | "argsDiff" | "riskTier" | "status">): boolean {
  if (p.status && p.status !== "PENDING") return false;
  return riskOf(p).risk === "low";
}

/**
 * Given a set of proposals and a candidate selection of ids, return the ids that
 * MAY be bulk-approved (destructive/high-risk excluded by construction) plus the
 * excluded ids. Capped at BULK_APPROVE_CAP.
 */
export function resolveBulkSelection(
  proposals: Pick<Proposal, "id" | "tool" | "argsDiff" | "riskTier" | "status">[],
  selectedIds: Set<string> | string[],
): { approvable: string[]; excluded: string[]; capped: boolean } {
  const sel = new Set(selectedIds);
  const approvable: string[] = [];
  const excluded: string[] = [];
  for (const p of proposals) {
    if (!sel.has(p.id)) continue;
    if (isBulkApprovable(p)) approvable.push(p.id);
    else excluded.push(p.id);
  }
  const capped = approvable.length > BULK_APPROVE_CAP;
  return { approvable: approvable.slice(0, BULK_APPROVE_CAP), excluded, capped };
}

/** Summary of counts by tool for the bulk-approve confirmation dialog (AC-5). */
export function summarizeByTool(
  proposals: Pick<Proposal, "id" | "tool">[],
  ids: string[],
): { tool: string; count: number }[] {
  const idset = new Set(ids);
  const counts = new Map<string, number>();
  for (const p of proposals) {
    if (!idset.has(p.id)) continue;
    const tool = p.tool ?? "unknown";
    counts.set(tool, (counts.get(tool) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([tool, count]) => ({ tool, count }))
    .sort((a, b) => b.count - a.count);
}

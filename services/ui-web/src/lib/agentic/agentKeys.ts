/**
 * Per-module copilot specialist routing (Tier 2b).
 *
 * Maps the current route to the published agent-runtime specialist the copilot
 * drawer should converse with. The keys MUST stay within the allowlist in
 * src/app/api/copilot/message/route.ts (which itself mirrors agent-runtime's
 * catalog, app/agents/catalog.py) — an unknown key silently falls back to the
 * default agent server-side, so keep both lists in lockstep.
 *
 * Returning null means "no module specialist here": the API route then uses
 * its default agent (COPILOT_AGENT_KEY, the read-only analytics copilot).
 */
export function agentKeyForPath(pathname: string): string | null {
  // Longest-prefix first: batch scoring lives under /ml but has its own agent.
  if (pathname === "/ml/inference" || pathname.startsWith("/ml/inference/")) return "inference";
  if (pathname === "/ml" || pathname.startsWith("/ml/")) return "model-training";
  if (pathname === "/dashboards" || pathname.startsWith("/dashboards/")) return "dashboard-designer";
  if (pathname === "/data" || pathname.startsWith("/data/")) return "onboarding";
  return null;
}

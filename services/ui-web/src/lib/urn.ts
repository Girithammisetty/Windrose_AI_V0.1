/**
 * Derive the current resource URN from a route path so the copilot opens with
 * the right context (AC-3: /data/datasets/ds-9f2 → wr:<tenant>:dataset:dataset/ds-9f2).
 * Pure + unit tested.
 */
const ROUTE_URN: { pattern: RegExp; type: string }[] = [
  { pattern: /^\/data\/datasets\/([^/]+)/, type: "dataset" },
  { pattern: /^\/cases\/([^/]+)/, type: "case" },
  { pattern: /^\/dashboards\/([^/]+)/, type: "dashboard" },
  { pattern: /^\/ml\/experiments\/([^/]+)/, type: "experiment" },
  { pattern: /^\/ml\/runs\/([^/]+)/, type: "run" },
  { pattern: /^\/copilot\/runs\/([^/]+)/, type: "agent_run" },
];

export function routeUrnFor(pathname: string, tenantId: string): string | null {
  for (const { pattern, type } of ROUTE_URN) {
    const m = pathname.match(pattern);
    if (m) return `wr:${tenantId}:${type}:${type}/${m[1]}`;
  }
  return null;
}

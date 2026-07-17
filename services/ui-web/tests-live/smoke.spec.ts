import { test, expect, loginAs, logout, expectPageHealthy, PERSONAS } from "./fixtures";

/**
 * SMOKE: prove the live stack renders real data across every major module.
 *
 * Each route below is owned by a different downstream service, so a green run
 * asserts the whole chain (UI → BFF → that service → Postgres/warehouse) is up
 * and composing correctly for a real seeded persona. Assertions are structural
 * (page shell + header render, no error boundary, no auth bounce) — deep
 * data-shape assertions live in the journey + per-module specs.
 */

// route → the primary service it exercises (for readable failure labels).
const MODULE_ROUTES: Array<{ path: string; service: string }> = [
  { path: "/cases", service: "case-service" },
  { path: "/inbox", service: "agent-runtime (proposals)" },
  { path: "/copilot", service: "agent-runtime (copilot)" },
  { path: "/data/ingestions", service: "ingestion-service" },
  { path: "/data/connections", service: "ingestion-service (connections)" },
  { path: "/data/pipelines", service: "pipeline-orchestrator" },
  { path: "/data/pipelines/runs", service: "pipeline-orchestrator (runs)" },
  { path: "/data/pipelines/schedules", service: "pipeline-orchestrator (schedules)" },
  { path: "/data/queries", service: "query-service" },
  { path: "/data/semantic-models", service: "semantic-service" },
  { path: "/dashboards", service: "chart-service" },
  { path: "/ml/eval", service: "eval-service" },
  { path: "/admin/users", service: "identity-service" },
  { path: "/admin/roles", service: "rbac-service (roles)" },
  { path: "/admin/groups", service: "rbac-service (groups)" },
  { path: "/admin/workspaces", service: "rbac-service (workspaces)" },
  { path: "/admin/teams", service: "rbac-service (teams)" },
  { path: "/admin/audit", service: "audit-service" },
  { path: "/admin/usage", service: "usage-service" },
  { path: "/admin/tools", service: "tool-registry" },
  { path: "/admin/memory", service: "memory-service" },
  { path: "/admin/notifications", service: "notification-service" },
  { path: "/admin/ai-gateway/providers", service: "ai-gateway" },
];

test.describe("smoke: live stack renders across all modules", () => {
  test("admin persona authenticates through the real auth path", async ({ page }) => {
    await loginAs(page, PERSONAS().admin);
    // loginAs already asserts the Welcome home; also confirm the tenant chrome.
    await expect(page.getByRole("heading", { name: /welcome/i })).toBeVisible();
  });

  for (const { path, service } of MODULE_ROUTES) {
    test(`renders ${path}  [${service}]`, async ({ page }) => {
      await loginAs(page, PERSONAS().admin);
      const resp = await page.goto(path, { waitUntil: "domcontentloaded" });
      // The Next route itself must not 5xx.
      expect(resp?.status() ?? 0, `${path} document status`).toBeLessThan(500);
      await expectPageHealthy(page, { notRedirectedFrom: path });
    });
  }
});

test.describe("smoke: auth posture", () => {
  test("fail-closed: unauthenticated access to a protected route redirects to /login", async ({ page }) => {
    await logout(page);
    await page.goto("/cases");
    await page.waitForURL("**/login**");
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("a non-admin seeded persona (adjuster) can authenticate and reach home", async ({ page }) => {
    // Proves differentiated personas work end-to-end (not just the super-scoped admin).
    await loginAs(page, PERSONAS().adjuster);
    await expect(page.getByRole("heading", { name: /welcome/i })).toBeVisible();
    // Their day-job surface loads.
    await page.goto("/cases");
    await expectPageHealthy(page, { notRedirectedFrom: "/cases" });
  });
});
